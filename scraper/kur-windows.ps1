# İndirim Avcısı - yerel tarayıcı kurulumu (Windows)
#
# n11 ve MediaMarkt GitHub'ın sunucularını engellediği için bu iki mağaza
# senin bilgisayarında taranır. Bu betik:
#   1. Repoyu %LOCALAPPDATA%\IndirimAvcisi klasörüne indirir (varsa günceller)
#   2. Görev Zamanlayıcı'ya 30 dakikada bir çalışan bir görev ekler (:15 ve :45'te)
#   3. Bir kez çalıştırıp sonucu gösterir
#
# Kaldırmak için:  Unregister-ScheduledTask -TaskName 'IndirimAvcisi Yerel Tarama'

$ErrorActionPreference = 'Stop'
$repo = 'https://github.com/Enskygr234/indirim-avcisi.git'
$dir = Join-Path $env:LOCALAPPDATA 'IndirimAvcisi'
$taskName = 'IndirimAvcisi Yerel Tarama'

foreach ($cmd in 'git') {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd bulunamadı, önce kurulmalı." }
}

# Konsol penceresi açmayan pythonw.exe'yi bul
$python = Get-Command python -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notlike '*WindowsApps*' } | Select-Object -First 1
if (-not $python) { throw 'Python bulunamadı (python.org sürümü gerekli, Microsoft Store kısayolu yetmez).' }
$pythonw = Join-Path (Split-Path $python.Source) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "pythonw.exe bulunamadı: $pythonw" }

if (Test-Path (Join-Path $dir '.git')) {
    Write-Host "Güncelleniyor: $dir"
    git -C $dir pull -q --ff-only
} else {
    Write-Host "İndiriliyor: $dir"
    git clone -q $repo $dir
}

$script = Join-Path $dir 'scraper\local_run.py'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $dir
$start = (Get-Date).Date.AddHours((Get-Date).Hour).AddMinutes(15)
if ($start -lt (Get-Date)) { $start = $start.AddMinutes(30) }
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 30)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force `
    -Description "n11 ve MediaMarkt indirimlerini tarar, $repo data dalına gönderir. Kayıt: $dir\.local-data.log" | Out-Null
Write-Host "Görev kuruldu: '$taskName' (ilk çalışma $($start.ToString('HH:mm')), sonra 30 dakikada bir)"

Write-Host 'İlk tarama yapılıyor (2-3 dakika sürer)...'
& $python.Source $script | Select-String -Pattern 'Toplam|OK -|HATA -|gönder|hata|vazgeç'
if ($LASTEXITCODE -eq 0) {
    Write-Host 'Hazır. İndirimler birkaç dakika içinde uygulamada görünür.' -ForegroundColor Green
} else {
    Write-Host "Tarama bitti ama gönderim başarısız oldu. Ayrıntı: $dir\.local-data.log" -ForegroundColor Yellow
}
