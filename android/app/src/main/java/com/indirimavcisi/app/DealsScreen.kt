package com.indirimavcisi.app

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import coil.compose.AsyncImage
import java.text.NumberFormat
import java.time.Duration
import java.time.Instant
import java.util.Locale

// ---- Renkler: sıcak koyu zemin, mercan vurgu, altın fiyat ----
private val Ink = Color(0xFF14110F)
private val CardBg = Color(0xFF201B18)
private val CardLine = Color(0xFF2E2723)
private val Accent = Color(0xFFFF5A36)
private val Hot = Color(0xFFE5243B)
private val Gold = Color(0xFFFFC24B)
private val TextMain = Color(0xFFF6EFEA)
private val TextMuted = Color(0xFFA39A94)

@Composable
fun IndirimTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Accent,
            background = Ink,
            surface = Ink,
            onBackground = TextMain,
            onSurface = TextMain,
        ),
        content = content,
    )
}

private val discountSteps = listOf(0, 20, 40, 60)

@Composable
fun DealsScreen(vm: DealsViewModel = viewModel()) {
    val state by vm.state.collectAsState()
    val context = LocalContext.current
    var minDiscount by rememberSaveable { mutableIntStateOf(0) }
    var category by rememberSaveable { mutableStateOf<String?>(null) }
    var store by rememberSaveable { mutableStateOf<String?>(null) }

    val feed = state.feed
    val categories = remember(feed) {
        feed?.deals?.map { it.category }?.distinct()?.sorted() ?: emptyList()
    }
    val stores = remember(feed) {
        feed?.deals?.map { it.store }?.distinct()?.sorted() ?: emptyList()
    }
    val visible = remember(feed, minDiscount, category, store) {
        feed?.deals?.filter {
            it.discount >= minDiscount &&
                (category == null || it.category == category) &&
                (store == null || it.store == store)
        } ?: emptyList()
    }

    Scaffold(containerColor = Ink) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item {
                Header(
                    updatedAt = feed?.updatedAt,
                    total = feed?.deals?.size ?: 0,
                    loading = state.loading,
                    onRefresh = { vm.refresh() },
                )
            }

            // Bir mağaza taranamadıysa kullanıcıya dürüstçe söyle
            val broken = feed?.sources?.filter { !it.ok } ?: emptyList()
            if (broken.isNotEmpty()) {
                item {
                    Notice(
                        "⚠ " + broken.joinToString { it.name.replaceFirstChar { c -> c.uppercase() } } +
                            " şu an taranamadı, gösterilen indirimler eski olabilir."
                    )
                }
            }
            state.error?.let { msg -> item { Notice(msg) } }

            item {
                LazyRow(
                    contentPadding = PaddingValues(horizontal = 16.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(discountSteps) { step ->
                        Pill(
                            text = if (step == 0) "Tüm indirimler" else "%$step+",
                            selected = minDiscount == step,
                            onClick = { minDiscount = step },
                        )
                    }
                }
            }
            if (stores.size > 1) {
                item {
                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        item {
                            Pill("Tüm mağazalar", selected = store == null, onClick = { store = null })
                        }
                        items(stores) { s ->
                            Pill(s, selected = store == s, onClick = { store = s })
                        }
                    }
                }
            }
            if (categories.size > 1) {
                item {
                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        item {
                            Pill("Hepsi", selected = category == null, onClick = { category = null })
                        }
                        items(categories) { c ->
                            Pill(c, selected = category == c, onClick = { category = c })
                        }
                    }
                }
            }

            if (feed != null && visible.isEmpty()) {
                item {
                    Text(
                        "Bu filtreye uyan indirim yok.",
                        color = TextMuted,
                        modifier = Modifier.padding(24.dp),
                    )
                }
            }

            items(visible, key = { it.id }) { deal ->
                DealCard(deal, onClick = { openDeal(context, deal) })
            }
        }
    }
}

@Composable
private fun Header(updatedAt: String?, total: Int, loading: Boolean, onRefresh: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 20.dp, end = 12.dp, top = 16.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                "İndirim Avcısı",
                color = TextMain,
                fontSize = 30.sp,
                fontWeight = FontWeight.Black,
            )
            Text(
                if (updatedAt == null) "Liste yükleniyor…"
                else "$total indirim · ${relativeTime(updatedAt)} güncellendi",
                color = TextMuted,
                fontSize = 13.sp,
            )
        }
        Box(
            modifier = Modifier
                .size(44.dp)
                .clip(CircleShape)
                .background(CardBg)
                .clickable(enabled = !loading, onClick = onRefresh),
            contentAlignment = Alignment.Center,
        ) {
            if (loading) {
                CircularProgressIndicator(
                    color = Accent,
                    strokeWidth = 2.dp,
                    modifier = Modifier.size(20.dp),
                )
            } else {
                Icon(Icons.Filled.Refresh, contentDescription = "Yenile", tint = TextMain)
            }
        }
    }
}

@Composable
private fun Notice(text: String) {
    Text(
        text,
        color = TextMain,
        fontSize = 13.sp,
        modifier = Modifier
            .padding(horizontal = 16.dp)
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(Color(0xFF3A2A1E))
            .padding(12.dp),
    )
}

@Composable
private fun Pill(text: String, selected: Boolean, onClick: () -> Unit) {
    Text(
        text,
        color = if (selected) Ink else TextMain,
        fontSize = 13.sp,
        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .background(if (selected) Gold else CardBg)
            .border(1.dp, if (selected) Gold else CardLine, RoundedCornerShape(50))
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    )
}

@Composable
private fun DealCard(deal: Deal, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .padding(horizontal = 16.dp)
            .fillMaxWidth()
            .clip(RoundedCornerShape(18.dp))
            .background(CardBg)
            .border(1.dp, CardLine, RoundedCornerShape(18.dp))
            .clickable(onClick = onClick)
            .padding(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(100.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(Color.White),
        ) {
            if (deal.image != null) {
                AsyncImage(
                    model = deal.image,
                    contentDescription = deal.title,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(4.dp),
                )
            }
            Text(
                "-%${deal.discount}",
                color = Color.White,
                fontSize = 13.sp,
                fontWeight = FontWeight.Black,
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .background(
                        if (deal.discount >= 50) Hot else Accent,
                        RoundedCornerShape(bottomEnd = 10.dp),
                    )
                    .padding(horizontal = 7.dp, vertical = 3.dp),
            )
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                listOf(deal.store.uppercase(), deal.brand).filter { it.isNotBlank() }.joinToString(" · "),
                color = TextMuted,
                fontSize = 11.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                deal.title,
                color = TextMain,
                fontSize = 14.sp,
                lineHeight = 18.sp,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.Bottom) {
                Text(
                    formatPrice(deal.price),
                    color = Gold,
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                )
                if (deal.oldPrice != null) {
                    Spacer(Modifier.width(8.dp))
                    Text(
                        formatPrice(deal.oldPrice),
                        color = TextMuted,
                        fontSize = 12.sp,
                        textDecoration = TextDecoration.LineThrough,
                        modifier = Modifier.padding(bottom = 2.dp),
                    )
                }
            }
        }
    }
}

private val trLocale: Locale = Locale.forLanguageTag("tr-TR")

private fun formatPrice(value: Double): String {
    val nf = NumberFormat.getNumberInstance(trLocale)
    nf.minimumFractionDigits = if (value % 1.0 == 0.0) 0 else 2
    nf.maximumFractionDigits = 2
    return nf.format(value) + " TL"
}

private fun relativeTime(iso: String): String {
    val t = try {
        Instant.parse(iso)
    } catch (e: Exception) {
        return "bilinmeyen zamanda"
    }
    val mins = Duration.between(t, Instant.now()).toMinutes()
    return when {
        mins < 1 -> "az önce"
        mins < 60 -> "$mins dk önce"
        mins < 60 * 24 -> "${mins / 60} saat önce"
        else -> "${mins / (60 * 24)} gün önce"
    }
}

private fun openDeal(context: Context, deal: Deal) {
    if (!SafeSites.isSafe(deal.url)) {
        Toast.makeText(context, "Bu link güvenli mağaza listesinde değil, açılmadı.", Toast.LENGTH_LONG).show()
        return
    }
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(deal.url)))
    } catch (e: ActivityNotFoundException) {
        Toast.makeText(context, "Linki açacak bir tarayıcı bulunamadı.", Toast.LENGTH_SHORT).show()
    }
}
