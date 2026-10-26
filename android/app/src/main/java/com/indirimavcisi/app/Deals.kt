package com.indirimavcisi.app

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.FileNotFoundException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL

/** Tarayıcının GitHub'a yazdığı indirim listesi. */
const val DATA_URL =
    "https://raw.githubusercontent.com/Enskygr234/indirim-avcisi/data/deals.json"

data class Deal(
    val id: String,
    val store: String,
    val title: String,
    val brand: String,
    val price: Double,
    val oldPrice: Double?,
    val discount: Int,
    val image: String?,
    val url: String,
    val category: String,
)

data class SourceStatus(
    val name: String,
    val ok: Boolean,
    val count: Int,
    val error: String?,
)

data class DealsFeed(
    val updatedAt: String?,
    val sources: List<SourceStatus>,
    val deals: List<Deal>,
)

/** Uygulama sadece bu güvenilir mağazaların sayfalarını açar. */
object SafeSites {
    private val hosts = listOf("trendyol.com", "hepsiburada.com", "amazon.com.tr", "n11.com", "mediamarkt.com.tr")

    fun isSafe(url: String): Boolean {
        val uri = try {
            URI(url)
        } catch (e: Exception) {
            return false
        }
        if (uri.scheme != "https") return false
        val host = uri.host?.lowercase() ?: return false
        return hosts.any { host == it || host.endsWith(".$it") }
    }
}

object FeedParser {
    fun parse(text: String): DealsFeed {
        val root = JSONObject(text)

        val deals = ArrayList<Deal>()
        val arr = root.optJSONArray("deals")
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val o = arr.optJSONObject(i) ?: continue
                val url = o.optString("url")
                if (!SafeSites.isSafe(url)) continue // güvensiz link asla gösterilmez
                deals.add(
                    Deal(
                        id = o.optString("id"),
                        store = o.optString("store"),
                        title = o.optString("title"),
                        brand = if (o.isNull("brand")) "" else o.optString("brand"),
                        price = o.optDouble("price", 0.0),
                        oldPrice = if (o.isNull("oldPrice")) null else o.optDouble("oldPrice"),
                        discount = o.optInt("discount"),
                        image = o.optString("image").takeIf { it.startsWith("https://") },
                        url = url,
                        category = if (o.isNull("category")) "Diğer" else o.optString("category"),
                    )
                )
            }
        }

        val sources = ArrayList<SourceStatus>()
        val src = root.optJSONObject("sources")
        if (src != null) {
            val keys = src.keys()
            while (keys.hasNext()) {
                val name = keys.next()
                val s = src.optJSONObject(name) ?: continue
                sources.add(
                    SourceStatus(
                        name = name,
                        ok = s.optBoolean("ok"),
                        count = s.optInt("count"),
                        error = if (s.isNull("error")) null else s.optString("error"),
                    )
                )
            }
        }

        val updated = if (root.isNull("updatedAt")) null else root.optString("updatedAt")
        return DealsFeed(updated, sources, deals)
    }
}

data class UiState(
    val feed: DealsFeed? = null,
    val loading: Boolean = false,
    val error: String? = null,
)

class DealsViewModel(app: Application) : AndroidViewModel(app) {
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state

    private val cacheFile = File(app.filesDir, "deals.json")

    init {
        // Önce telefondaki son listeyi göster, sonra internetten yenile
        viewModelScope.launch {
            val cached = withContext(Dispatchers.IO) {
                try {
                    if (cacheFile.exists()) FeedParser.parse(cacheFile.readText()) else null
                } catch (e: Exception) {
                    null
                }
            }
            if (cached != null && _state.value.feed == null) {
                _state.value = _state.value.copy(feed = cached)
            }
        }
        refresh()
    }

    fun refresh() {
        if (_state.value.loading) return
        _state.value = _state.value.copy(loading = true, error = null)
        viewModelScope.launch {
            try {
                val text = withContext(Dispatchers.IO) { download() }
                val feed = withContext(Dispatchers.Default) { FeedParser.parse(text) }
                withContext(Dispatchers.IO) { cacheFile.writeText(text) }
                _state.value = UiState(feed = feed)
            } catch (e: FileNotFoundException) {
                _state.value = _state.value.copy(
                    loading = false,
                    error = "Henüz indirim listesi yok. Tarayıcı ilk turunu bitirince burada görünecek.",
                )
            } catch (e: Exception) {
                _state.value = _state.value.copy(
                    loading = false,
                    error = "İnternete bağlanılamadı. İnternetini kontrol edip yenile.",
                )
            }
        }
    }

    private fun download(): String {
        // Dakikada bir değişen parametre: eski önbellek yerine güncel liste gelsin
        val url = URL("$DATA_URL?t=${System.currentTimeMillis() / 60_000}")
        val conn = url.openConnection() as HttpURLConnection
        conn.connectTimeout = 15_000
        conn.readTimeout = 20_000
        conn.setRequestProperty("Accept", "application/json")
        return try {
            conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }
}
