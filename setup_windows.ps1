$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$base = Join-Path $env:LOCALAPPDATA 'CryptoDesk'
$stage = $null
try {
    # Never install while a server or research worker from this installation is alive.
    $probe = New-Object System.Net.Sockets.TcpClient
    try { $probe.Connect('127.0.0.1',8877); $inUse=$true } catch { $inUse=$false } finally { $probe.Dispose() }
    if ($inUse) { throw 'Crypto Desk e aperto. Premi Esci dentro l''app, attendi il salvataggio e riapri questo aggiornamento.' }
    $pidFile = Join-Path $base 'running.pid'
    if (Test-Path $pidFile) {
        $enginePid = 0
        if ([int]::TryParse((Get-Content $pidFile -Raw).Trim(),[ref]$enginePid)) {
            $proc = Get-Process -Id $enginePid -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -match '^python') { throw 'La ricerca sta ancora salvando. Attendi qualche secondo e riapri questo file.' }
        }
    }
    $python = $null
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { $python = & $py.Source -3 -c "import sys; assert sys.version_info >= (3,10); print(sys.executable)" 2>$null; if ($LASTEXITCODE -ne 0) { $python=$null } }
    if (-not $python) {
        $py = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($py) { $python = & $py.Source -c "import sys; assert sys.version_info >= (3,10); print(sys.executable)" 2>$null; if ($LASTEXITCODE -ne 0) { $python=$null } }
    }
    if (-not $python) { throw 'Serve Python 3.10 o successivo gia installato. Installa Python e poi riapri questo file.' }
    $pythonw = Join-Path (Split-Path ([string]$python).Trim()) 'pythonw.exe'
    if (-not (Test-Path $pythonw)) { throw 'Python trovato, ma manca pythonw.exe. Ripara l''installazione di Python.' }
    $text = [IO.File]::ReadAllText($env:CRYPTO_DESK_SETUP)
    $marker = '::PAYLOAD'
    $markerIndex = $text.IndexOf($marker)
    if ($markerIndex -lt 0) { throw 'Pacchetto mancante nel file installatore.' }
    $encoded = $text.Substring($markerIndex + $marker.Length).Trim()
    $bytes = [Convert]::FromBase64String($encoded)
    $sha = [Security.Cryptography.SHA256]::Create()
    $hash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    if ($hash -ne '__HASH__') { throw 'File di installazione incompleto o alterato. Scaricalo nuovamente.' }
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    $stage = Join-Path $base ('stage-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $archive = Join-Path $stage 'package.zip'
    [IO.File]::WriteAllBytes($archive,$bytes)
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $stage 'app')
    $data = Join-Path $base 'data'
    # First install only: offer to copy old data, never delete the old installation.
    if (-not (Test-Path $data)) {
        $answer = [Windows.Forms.MessageBox]::Show('Hai una precedente cartella Crypto Desk con ricerche da conservare? Seleziona Si e indica la sua cartella data.','Crypto Desk - conserva i dati','YesNo','Question')
        if ($answer -eq 'Yes') {
            $picker = New-Object Windows.Forms.FolderBrowserDialog
            $picker.Description = 'Seleziona la cartella DATA della precedente versione'
            if ($picker.ShowDialog() -ne 'OK') { throw 'Installazione interrotta. Nessun dato precedente modificato.' }
            $old = $picker.SelectedPath
            if (-not ((Test-Path (Join-Path $old 'portfolio.json')) -or (Test-Path (Join-Path $old 'deep_plan.json')) -or (Test-Path (Join-Path $old 'deep_research')))) { throw 'La cartella scelta non contiene dati riconoscibili di Crypto Desk. Riprova scegliendo data.' }
            Copy-Item -LiteralPath $old -Destination (Join-Path $stage 'migrated-data') -Recurse
            Move-Item -LiteralPath (Join-Path $stage 'migrated-data') -Destination $data
        } else { New-Item -ItemType Directory -Path $data | Out-Null }
    }
    $release = '0.8-'+$hash.Substring(0,12)
    $versions = Join-Path $base 'versions'
    New-Item -ItemType Directory -Force -Path $versions | Out-Null
    $target = Join-Path $versions $release
    if (-not (Test-Path $target)) { Move-Item -LiteralPath (Join-Path $stage 'app') -Destination $target }
    Copy-Item -LiteralPath (Join-Path $target 'launcher.pyw') -Destination (Join-Path $base 'launcher.pyw') -Force
    Copy-Item -LiteralPath (Join-Path $target 'updater.py') -Destination (Join-Path $base 'updater.py') -Force
    [IO.File]::WriteAllText((Join-Path $base 'current.new'),$release)
    $current = Join-Path $base 'current.txt'
    if (Test-Path $current) { [IO.File]::Replace((Join-Path $base 'current.new'),$current,(Join-Path $base 'previous.txt')) } else { Move-Item (Join-Path $base 'current.new') $current }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Crypto Desk.lnk'))
    $link.TargetPath=$pythonw
    $link.Arguments='"'+(Join-Path $base 'launcher.pyw')+'"'
    $link.WorkingDirectory=$base
    $link.Description='Crypto Desk - ricerca e simulazione'
    $link.IconLocation=(Join-Path $target 'app.ico')
    $link.Save()
    Remove-Item -LiteralPath $stage -Recurse -Force; $stage=$null
    Start-Process -FilePath $pythonw -ArgumentList ('"'+(Join-Path $base 'launcher.pyw')+'"') -WorkingDirectory $base
    [Windows.Forms.MessageBox]::Show('Crypto Desk aggiornato. Da ora aprilo sempre dall''icona Crypto Desk sul desktop. Gli aggiornamenti futuri si installano automaticamente all’apertura e conservano i dati.','Crypto Desk','OK','Information') | Out-Null
} catch {
    [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Crypto Desk - installazione non completata','OK','Error') | Out-Null
    exit 1
} finally {
    if ($stage -and (Test-Path $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
