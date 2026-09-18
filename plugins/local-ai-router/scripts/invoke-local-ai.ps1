[CmdletBinding(DefaultParameterSetName = 'PromptOnly')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('coder', 'reasoner')]
    [string]$Role,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Prompt,

    [Parameter(ParameterSetName = 'WithFile', Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$InputPath,

    [ValidateRange(1000, 12000)]
    [int]$MaxOutputChars = 6000,

    [ValidateRange(4000, 20000)]
    [int]$ChunkChars = 12000
)

$ErrorActionPreference = 'Stop'
$localAiRoot = 'D:\CodexLocalAI'
$localAiScript = Join-Path $localAiRoot 'local-ai.ps1'
$maximumInputBytes = 10MB

function Invoke-Worker {
    param([Parameter(Mandatory = $true)][string]$WorkerPrompt)

    $result = & $localAiScript -Role $Role -Action chat -Prompt $WorkerPrompt
    return ($result | Out-String).Trim()
}

function Limit-Text {
    param([Parameter(Mandatory = $true)][string]$Text)

    if ($Text.Length -le $MaxOutputChars) {
        return $Text
    }
    return $Text.Substring(0, $MaxOutputChars).TrimEnd() + "`n[local output truncated]"
}

try {
    if (-not (Test-Path -LiteralPath $localAiScript -PathType Leaf)) {
        throw "Local AI launcher not found: $localAiScript"
    }

    if ($PSCmdlet.ParameterSetName -eq 'PromptOnly') {
        $workerPrompt = @"
$Prompt

Return only a concise result for a supervising Codex agent. Do not reveal chain-of-thought. State assumptions, uncertainties, affected files, and checks only when relevant.
"@
        Limit-Text -Text (Invoke-Worker -WorkerPrompt $workerPrompt)
        exit 0
    }

    $resolvedInputPath = (Resolve-Path -LiteralPath $InputPath -ErrorAction Stop).Path
    $inputItem = Get-Item -LiteralPath $resolvedInputPath -ErrorAction Stop
    if (-not $inputItem.PSIsContainer -and $inputItem.Length -gt $maximumInputBytes) {
        throw "Input file exceeds the 10 MB safety limit."
    }
    if ($inputItem.PSIsContainer) {
        throw "InputPath must identify one text file, not a directory."
    }

    $inputText = Get-Content -LiteralPath $resolvedInputPath -Raw -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($inputText)) {
        throw "Input file is empty."
    }

    if ($inputText.Length -le $ChunkChars) {
        $workerPrompt = @"
$Prompt

Source content:
---
$inputText
---
Return only a concise result for a supervising Codex agent. Do not reveal chain-of-thought.
"@
        Limit-Text -Text (Invoke-Worker -WorkerPrompt $workerPrompt)
        exit 0
    }

    $chunkCount = [Math]::Ceiling($inputText.Length / [double]$ChunkChars)
    $chunkSummaries = [System.Collections.Generic.List[string]]::new()
    for ($index = 0; $index -lt $chunkCount; $index++) {
        $start = $index * $ChunkChars
        $length = [Math]::Min($ChunkChars, $inputText.Length - $start)
        $chunk = $inputText.Substring($start, $length)
        $chunkPrompt = @"
Task: $Prompt

Process source chunk $($index + 1) of $chunkCount. Preserve concrete facts, identifiers, constraints, errors, and unresolved uncertainty. Return at most 1200 characters. Do not reveal chain-of-thought.

Source chunk:
---
$chunk
---
"@
        $chunkSummaries.Add((Invoke-Worker -WorkerPrompt $chunkPrompt))
    }

    $combinedSummaries = $chunkSummaries -join "`n`n"
    $reducePrompt = @"
Task: $Prompt

Merge the following chunk results into one concise, non-repetitive result. Preserve concrete evidence and uncertainty. Return no more than $MaxOutputChars characters and do not reveal chain-of-thought.

Chunk results:
---
$combinedSummaries
---
"@
    Limit-Text -Text (Invoke-Worker -WorkerPrompt $reducePrompt)
}
catch {
    Write-Error "Local AI routing failed: $($_.Exception.Message)"
    exit 1
}
finally {
    if (Test-Path -LiteralPath $localAiScript -PathType Leaf) {
        try {
            & $localAiScript -Action stop | Out-Null
        }
        catch {
            Write-Warning "Unable to stop the local AI service: $($_.Exception.Message)"
        }
    }
}
