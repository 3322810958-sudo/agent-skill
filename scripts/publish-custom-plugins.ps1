[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Push,
    [string]$CommitMessage
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Fail([string]$Message) {
    throw $Message
}

function Assert-ChildPath([string]$Parent, [string]$Child, [string]$Label) {
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $childFull = [System.IO.Path]::GetFullPath($Child)
    if (-not $childFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "$Label is outside the approved root."
    }
}

function Invoke-Git([string[]]$Arguments, [switch]$AllowFailure) {
    $output = & git @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowFailure) {
        Fail "Git command failed: git $($Arguments -join ' ')"
    }
    return [pscustomobject]@{ Code = $code; Output = @($output) }
}

function Test-PluginManifest([string]$PluginRoot, [string]$ExpectedName) {
    $manifestPath = Join-Path $PluginRoot '.codex-plugin\plugin.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        Fail "Plugin '$ExpectedName' is missing .codex-plugin/plugin.json."
    }

    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        Fail "Plugin '$ExpectedName' has invalid plugin.json."
    }

    if ($manifest.name -ne $ExpectedName) {
        Fail "Plugin manifest name does not match allowlist entry '$ExpectedName'."
    }
    if ([string]::IsNullOrWhiteSpace([string]$manifest.version) -or
        [string]$manifest.version -notmatch '^\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$') {
        Fail "Plugin '$ExpectedName' has an invalid version."
    }
    if (-not $manifest.author -or [string]::IsNullOrWhiteSpace([string]$manifest.author.name)) {
        Fail "Plugin '$ExpectedName' has no author name."
    }
    if ([string]::IsNullOrWhiteSpace([string]$manifest.skills)) {
        Fail "Plugin '$ExpectedName' has no skills path."
    }
}

function Test-PublishableFiles(
    [string]$PluginRoot,
    [string[]]$AllowedExtensions
) {
    $blockedSegments = @(
        '.git', '.venv', '__pycache__', 'backups', 'cache', 'credentials',
        'data', 'logs', 'models', 'node_modules', 'pay', 'payment', 'secrets', 'temp'
    )
    $blockedExtensions = @(
        '.bin', '.env', '.gif', '.gguf', '.jpeg', '.jpg', '.key', '.mov', '.mp3',
        '.mp4', '.onnx', '.otf', '.p12', '.pem', '.pfx', '.png', '.safetensors',
        '.ttf', '.wav', '.webp', '.woff', '.woff2', '.zip'
    )
    $secretRules = [ordered]@{
        'private-key' = '-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'
        'github-token' = '\bgh[pousr]_[A-Za-z0-9]{20,}\b'
        'generic-sk-token' = '\bsk-[A-Za-z0-9_-]{16,}\b'
        'assigned-secret' = '(?i)(api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*["''][^"'']{8,}["'']'
    }

    $files = @(Get-ChildItem -LiteralPath $PluginRoot -Recurse -File -Force)
    if ($files.Count -eq 0) {
        Fail "Plugin folder is empty: $PluginRoot"
    }

    foreach ($file in $files) {
        $relative = $file.FullName.Substring($PluginRoot.Length).TrimStart('\')
        $segments = $relative -split '[\\/]'
        foreach ($segment in $segments) {
            if ($blockedSegments -contains $segment.ToLowerInvariant()) {
                Fail "Blocked path category in plugin file: $relative"
            }
        }

        $extension = $file.Extension.ToLowerInvariant()
        if ($blockedExtensions -contains $extension -or $AllowedExtensions -notcontains $extension) {
            Fail "Unapproved file type in plugin: $relative"
        }
        if ($file.Length -gt 2MB) {
            Fail "Plugin file exceeds the 2 MiB publication limit: $relative"
        }

        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
        foreach ($rule in $secretRules.GetEnumerator()) {
            if ($content -match $rule.Value) {
                Fail "Sensitive-content rule '$($rule.Key)' matched: $relative"
            }
        }
    }
}

$scriptRoot = Split-Path -Parent $PSCommandPath
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot '..'))
$configPath = Join-Path $repoRoot 'config\publish-allowlist.json'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    Fail 'Publication allowlist is missing.'
}

$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$expectedRepo = [System.IO.Path]::GetFullPath([string]$config.repositoryRoot)
if ($repoRoot.TrimEnd('\') -ne $expectedRepo.TrimEnd('\')) {
    Fail 'This script is not running from the approved repository path.'
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.git') -PathType Container)) {
    Fail 'Approved repository is not initialized as a Git repository.'
}

$gitCheck = Invoke-Git -Arguments @('-C', $repoRoot, 'status', '--porcelain')
if ($gitCheck.Output.Count -gt 0 -and -not $DryRun) {
    Fail 'Repository has uncommitted changes. Commit or discard them before publishing.'
}

$allowedExtensions = @($config.allowedExtensions | ForEach-Object { ([string]$_).ToLowerInvariant() })
$stagingRoot = Join-Path 'D:\CodexLocalAI\temp' ("plugin-publish-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

try {
    foreach ($plugin in @($config.plugins)) {
        $name = [string]$plugin.name
        $source = [System.IO.Path]::GetFullPath([string]$plugin.source)
        Assert-ChildPath -Parent 'D:\CodexLocalAI\plugins' -Child $source -Label "Source for '$name'"
        if (-not (Test-Path -LiteralPath $source -PathType Container)) {
            Fail "Approved source is missing for plugin '$name'."
        }

        Test-PluginManifest -PluginRoot $source -ExpectedName $name
        Test-PublishableFiles -PluginRoot $source -AllowedExtensions $allowedExtensions

        $stagePlugin = Join-Path $stagingRoot $name
        New-Item -ItemType Directory -Force -Path $stagePlugin | Out-Null
        Get-ChildItem -LiteralPath $source -Recurse -File -Force | ForEach-Object {
            $relative = $_.FullName.Substring($source.Length).TrimStart('\')
            $target = Join-Path $stagePlugin $relative
            $targetParent = Split-Path -Parent $target
            New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target
        }
        Write-Output "Validated: $name"
    }

    if ($DryRun) {
        Write-Output 'Dry run passed. No files, commits, or remotes were changed.'
        return
    }

    $pluginsDestination = Join-Path $repoRoot 'plugins'
    Assert-ChildPath -Parent $repoRoot -Child $pluginsDestination -Label 'Plugin destination'
    if (Test-Path -LiteralPath $pluginsDestination) {
        Remove-Item -LiteralPath $pluginsDestination -Recurse -Force
    }
    Move-Item -LiteralPath $stagingRoot -Destination $pluginsDestination
    $stagingRoot = $null

    Invoke-Git -Arguments @('-C', $repoRoot, 'add', '--', 'plugins') | Out-Null
    $diff = Invoke-Git -Arguments @('-C', $repoRoot, 'diff', '--cached', '--quiet') -AllowFailure
    if ($diff.Code -eq 0) {
        Write-Output 'No plugin changes detected. Nothing to commit.'
        return
    }
    if ($diff.Code -ne 1) {
        Fail 'Unable to determine whether staged plugin changes exist.'
    }

    if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
        $CommitMessage = 'chore: sync custom plugins ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    }
    Invoke-Git -Arguments @('-C', $repoRoot, 'commit', '-m', $CommitMessage) | Out-Null
    Write-Output 'Created a local Git commit.'

    if ($Push) {
        $remote = Invoke-Git -Arguments @('-C', $repoRoot, 'remote', 'get-url', 'origin') -AllowFailure
        if ($remote.Code -ne 0) {
            Fail 'Remote origin is not configured; the local commit was kept.'
        }
        Invoke-Git -Arguments @('-C', $repoRoot, 'push', 'origin', 'HEAD') | Out-Null
        Write-Output 'Pushed the commit to origin.'
    }
}
finally {
    if ($stagingRoot -and (Test-Path -LiteralPath $stagingRoot)) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
