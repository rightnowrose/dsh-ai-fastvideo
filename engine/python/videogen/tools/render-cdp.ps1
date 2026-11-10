# render-cdp.ps1 -- frame-by-frame renderer driven by the DevTools protocol.
#
# Launches Edge ONCE (headless), navigates to the page, then for every frame does
# Runtime.evaluate("window.seek(t)") + canvas.toDataURL(), writes PNGs, encodes mp4.
# Measured: ~33 ms/frame on a 1080x1920 page (vs ~1260 ms/frame with one browser
# launch per frame). The page contract is: window.seek(t), window.DUR, draw(t) pure.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File render-cdp.ps1 -Html page.html -Out out.mp4
#       -Width 1080 -Height 1920 -Fps 24 -Duration 5.79 -Mode canvas
param(
  [Parameter(Mandatory=$true)][string]$Html,
  [Parameter(Mandatory=$true)][string]$Out,
  [int]$Fps = 24,
  [double]$Duration = 0,
  [int]$Width = 1080,
  [int]$Height = 1920,
  [int]$Crf = 21,
  [string]$Preset = 'medium',
  [int]$Port = 9333,
  [ValidateSet('screenshot','canvas')][string]$Mode = 'canvas',
  [ValidateSet('png','jpeg')][string]$Format = 'png',
  [string]$ExtraQuery = "",
  [int]$Timeout = 900,
  [switch]$KeepFrames
)

$ErrorActionPreference = 'Continue'
$Edge   = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$Ffmpeg = 'D:\ffmpeg\ffmpeg-n9.0-latest-win64-gpl-9.0\bin\ffmpeg.exe'
if (-not (Test-Path $Edge))   { throw "Edge not found: $Edge" }
if (-not (Test-Path $Ffmpeg)) { throw "ffmpeg not found: $Ffmpeg" }

$Html = (Resolve-Path $Html).Path
if (-not [System.IO.Path]::IsPathRooted($Out)) { $Out = Join-Path (Get-Location).Path $Out }
$outDir = [System.IO.Path]::GetDirectoryName($Out)
if (-not $outDir) { $outDir = (Get-Location).Path }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$frameDir = Join-Path $env:TEMP ('vg_frames_' + [Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path $frameDir | Out-Null
# jpeg 只对 canvas 模式生效：screenshot 模式拿到的一律是 png
$fmt = if ($Mode -eq 'canvas') { $Format } else { 'png' }
$ext = if ($fmt -eq 'jpeg') { 'jpg' } else { 'png' }

$uriBase = ([System.Uri]$Html).AbsoluteUri
$url = "${uriBase}?t=0&w=$Width&h=$Height$ExtraQuery"

# ---------- 1. launch Edge once ----------
$ud = Join-Path $env:TEMP ("edge-cdp-$PID")
$edgeArgs = @(
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--hide-scrollbars', '--disable-extensions', '--allow-file-access-from-files',
  '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows',
  "--user-data-dir=$ud", "--remote-debugging-port=$Port", "--window-size=$Width,$Height", 'about:blank'
)
Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like "*remote-debugging-port=$Port*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$edgeProc = Start-Process -FilePath $Edge -ArgumentList $edgeArgs -PassThru
Write-Host "edge pid=$($edgeProc.Id) port=$Port"

$ws = $null; $cts = $null
try {
  $ver = $null
  for ($i = 0; $i -lt 100; $i++) {
    try { $ver = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2; break } catch { Start-Sleep -Milliseconds 200 }
  }
  if (-not $ver) { throw "CDP port $Port did not come up" }

  $list = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list" -TimeoutSec 5
  $page = @($list) | Where-Object { $_.type -eq 'page' } | Select-Object -First 1
  if (-not $page) { throw "no page target" }
  $wsUrl = $page.webSocketDebuggerUrl

  $ws = New-Object System.Net.WebSockets.ClientWebSocket
  $ws.Options.Proxy = $null
  $cts = New-Object System.Threading.CancellationTokenSource
  $ws.ConnectAsync([Uri]$wsUrl, $cts.Token).Wait()

  $script:CdpId = 0
  $script:IoTimeoutMs = 60000
  function Receive-Cdp {
    $buf = New-Object byte[] 131072
    $ms = New-Object System.IO.MemoryStream
    do {
      $seg = [System.ArraySegment[byte]]::new($buf)
      $task = $script:ws.ReceiveAsync($seg, $script:cts.Token)
      if (-not $task.Wait($script:IoTimeoutMs)) {
        try { $script:ws.Abort() } catch { }
        throw "CDP 读超时（${script:IoTimeoutMs}ms 没有响应，页面主线程可能卡死）"
      }
      $t = $task.Result
      if ($t.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) { throw "CDP connection closed" }
      $ms.Write($buf, 0, $t.Count)
    } while (-not $t.EndOfMessage)
    $txt = [System.Text.Encoding]::UTF8.GetString($ms.ToArray())
    $ms.Dispose()
    return ($txt | ConvertFrom-Json)
  }
  function Invoke-Cdp([string]$method, $params) {
    $script:CdpId++
    $id = $script:CdpId
    $json = @{ id = $id; method = $method; params = $params } | ConvertTo-Json -Depth 12 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $seg = [System.ArraySegment[byte]]::new($bytes)
    $script:ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $script:cts.Token).Wait()
    while ($true) {
      $m = Receive-Cdp
      if ($m.id -eq $id) {
        if ($m.error) { throw "CDP $method failed: $($m.error.message)" }
        return $m.result
      }
    }
  }

  Invoke-Cdp 'Page.enable' @{} | Out-Null
  Invoke-Cdp 'Runtime.enable' @{} | Out-Null
  Invoke-Cdp 'Emulation.setDeviceMetricsOverride' @{ width = $Width; height = $Height; deviceScaleFactor = 1; mobile = $false } | Out-Null
  Invoke-Cdp 'Page.navigate' @{ url = $url } | Out-Null

  for ($i = 0; $i -lt 150; $i++) {
    $r = Invoke-Cdp 'Runtime.evaluate' @{ expression = 'document.readyState + "|" + (typeof window.seek)'; returnByValue = $true }
    if ($r.result.value -eq 'complete|function') { break }
    Start-Sleep -Milliseconds 100
  }
  if ($r.result.value -ne 'complete|function') {
    throw "页面 15 秒内没准备好（readyState|seek = $($r.result.value)），这一屏跳过"
  }
  $script:Deadline = [DateTime]::Now.AddSeconds($Timeout)
  $chk = Invoke-Cdp 'Runtime.evaluate' @{ expression = 'String(window.DUR||"")'; returnByValue = $true }
  if ($Duration -le 0) {
    $d = 0.0
    if ([double]::TryParse($chk.result.value, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d) -and $d -gt 0) { $Duration = $d }
    else { throw "window.DUR unavailable, pass -Duration explicitly" }
  }
  $total = [int][Math]::Round($Duration * $Fps)
  Write-Host ("render {0} frames  {1}x{2} @{3}fps  {4:0.###}s  mode={5}" -f $total, $Width, $Height, $Fps, $Duration, $Mode)

  $sw = [Diagnostics.Stopwatch]::StartNew()
  $inv = [Globalization.CultureInfo]::InvariantCulture
  for ($i = 0; $i -lt $total; $i++) {
    if ([DateTime]::Now -gt $script:Deadline) {
      throw ("渲染超时：{0} 秒还没渲完（{1}/{2} 帧），这一屏放弃" -f $Timeout, $i, $total)
    }
    $t = [string]::Format($inv, '{0:0.00000}', ($i / $Fps))
    if ($Mode -eq 'screenshot') {
      Invoke-Cdp 'Runtime.evaluate' @{ expression = "window.seek($t)"; returnByValue = $true } | Out-Null
      $r = Invoke-Cdp 'Page.captureScreenshot' @{ format = 'png'; fromSurface = $true }
      $b64 = $r.data
    } else {
      $expr = "(()=>{window.seek($t);return document.getElementById('cv').toDataURL('image/$fmt',0.93).split(',')[1]})()"
      $r = Invoke-Cdp 'Runtime.evaluate' @{ expression = $expr; returnByValue = $true }
      $b64 = $r.result.value
    }
    $bytes = [Convert]::FromBase64String($b64)
    [System.IO.File]::WriteAllBytes((Join-Path $frameDir (('f{0:d4}.' -f $i) + $ext)), $bytes)
    if (($i + 1) % 24 -eq 0 -or $i -eq $total - 1) {
      $el = $sw.Elapsed.TotalSeconds
      Write-Host ("  frame {0}/{1}  {2:0.0}s  {3:0}ms/frame" -f ($i + 1), $total, $el, ($el * 1000 / ($i + 1)))
    }
  }
  $frameSec = $sw.Elapsed.TotalSeconds
} finally {
  if ($ws) { try { $ws.Dispose() } catch {} }
  if ($cts) { try { $cts.Dispose() } catch {} }
  Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*remote-debugging-port=$Port*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Remove-Item $ud -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "encode mp4..."
# JPEG 帧自带 full range，转回 tv range 再编码，否则部分播放器（手机 QQ）会发灰/过曝
$vArgs = @()
if ($fmt -eq 'jpeg') {
  $vArgs = @('-vf', 'scale=in_range=full:out_range=tv', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709')
}
& $Ffmpeg -y -v error -framerate $Fps -i (Join-Path $frameDir ('f%04d.' + $ext)) `
  @vArgs -c:v libx264 -preset $Preset -crf $Crf -pix_fmt yuv420p -movflags +faststart $Out
if ($LASTEXITCODE -ne 0) { throw "ffmpeg encode failed" }
if (-not $KeepFrames) { Remove-Item (Join-Path $frameDir ('*.' + $ext)) -Force -ErrorAction SilentlyContinue; Remove-Item $frameDir -Recurse -Force -ErrorAction SilentlyContinue }

$sz = [math]::Round((Get-Item $Out).Length / 1MB, 2)
Write-Host ("done: {0}  {1} MB  frames {2:0.0}s ({3:0}ms/frame)" -f $Out, $sz, $frameSec, ($frameSec * 1000 / $total))
