param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][int]$ApproachX,
    [Parameter(Mandatory)][int]$ApproachY,
    [Parameter(Mandatory)][int]$ArenaRight,
    [Parameter(Mandatory)][int]$ArenaTop,
    [string]$CascadeProject
)

$ErrorActionPreference = 'Stop'
$campaignFile = Get-Item -LiteralPath $Path
if ($campaignFile.Extension -ne '.bfc') { throw 'Expected a local BFC campaign.' }
$managed = 'F:\SteamLibrary\steamapps\common\Broforce\Broforce_beta_Data\Managed'
$assembly = [Reflection.Assembly]::LoadFrom((Join-Path $managed 'Assembly-CSharp.dll'))
$codec = $assembly.GetType('CLZF2', $true)
$original = [IO.File]::ReadAllBytes($campaignFile.FullName)
$blobOffset = [Array]::IndexOf($original, [byte]31)
if ($blobOffset -le 0) { throw 'BFC XML header has no payload separator.' }
$blob = [byte[]]::new($original.Length - $blobOffset)
[Array]::Copy($original, $blobOffset, $blob, 0, $blob.Length)
$document = [Xml.XmlDocument]::new()
$document.XmlResolver = $null
$decoded = $codec.GetMethod('Decompress').Invoke($null, (,$blob))
$document.Load([IO.MemoryStream]::new($decoded))

$bosses = $document.SelectNodes('//DoodadInfo[type="PureEvil"]')
$exits = $document.SelectNodes('//TriggerInfo[type="Area" and actions/TriggerActionInfo/name="Extraction"]')
if ($bosses.Count -ne 1 -or $exits.Count -ne 1) { throw 'Expected exactly one boss and one extraction trigger.' }
$boss = $bosses[0]
$exit = $exits[0]
$triggers = $exit.ParentNode
$oldDefeat = $triggers.SelectSingleNode('TriggerInfo[type="Entity" and tag="rift-satan"]')
if ($null -ne $oldDefeat) {
    $oldApproach = $triggers.SelectSingleNode("TriggerInfo[type='Area' and actions/TriggerActionInfo/triggerName='$($oldDefeat.name)']")
    if ($null -eq $oldApproach) { throw 'The existing boss gate is incomplete.' }
    $null = $triggers.RemoveChild($oldApproach)
    $null = $triggers.RemoveChild($oldDefeat)
}

function Set-Field($Node, [string]$Name, [string]$Value) {
    $field = $Node.SelectSingleNode($Name)
    if ($null -eq $field) {
        $field = $document.CreateElement($Name)
        $null = $Node.AppendChild($field)
    }
    $field.InnerText = $Value
}

function Set-GridPoint($Point, [int]$X, [int]$Y) {
    foreach ($field in @('collumn', 'c')) { Set-Field $Point $field $X }
    foreach ($field in @('row', 'r')) { Set-Field $Point $field $Y }
}

function Set-Activation($Trigger, [string]$Target) {
    $action = $Trigger.SelectSingleNode('actions/TriggerActionInfo')
    Set-Field $action 'name' 'Activate'
    Set-Field $action 'levelActionType' 'ActivateTrigger'
    Set-Field $action 'triggerName' $Target
}

$exitName = 'RiftExtractionThisAttempt'
$defeatName = 'RiftBossDefeatedThisAttempt'
$bossTag = 'rift-satan'
Set-Field $boss 'tag' $bossTag
Set-Field $exit 'name' $exitName
Set-Field $exit 'startEnabled' 'false'
$defeat = $exit.CloneNode($true)
Set-Field $defeat 'name' $defeatName
Set-Field $defeat 'type' 'Entity'
Set-Field $defeat 'tag' $bossTag
Set-Activation $defeat $exitName
$approach = $exit.CloneNode($true)
Set-Field $approach 'name' 'RiftBossApproachThisAttempt'
Set-Field $approach 'startEnabled' 'true'
Set-GridPoint $approach.SelectSingleNode('bottomLeft') $ApproachX $ApproachY
Set-GridPoint $approach.SelectSingleNode('upperRight') $ArenaRight $ArenaTop
Set-Activation $approach $defeatName
$null = $triggers.AppendChild($defeat)
$null = $triggers.AppendChild($approach)
$hazardIndex = 0
foreach ($trigger in $triggers.SelectNodes('TriggerInfo')) {
    # The engine still marks the trigger fired for this attempt. This flag only persists it across retries.
    Set-Field $trigger 'onlyTriggerOnce' 'false'
    $name = $trigger.SelectSingleNode('name')
    if ($null -eq $name -or [string]::IsNullOrEmpty($name.InnerText)) {
        $hazardIndex++
        Set-Field $trigger 'name' "RiftHazard$hazardIndex"
    }
}
$oilTrigger = $triggers.SelectSingleNode('TriggerInfo[name="RiftHazard3"]')
if ($null -ne $oilTrigger) {
    $oilActions = $oilTrigger.SelectSingleNode('actions')
    $firstBlast = $oilActions.SelectSingleNode('TriggerActionInfo[type="Collapse"]')
    if ($firstBlast.SelectSingleNode('targetPoint/collumn').InnerText -ne '164') {
        throw 'Unexpected refinery trigger target.'
    }
    foreach ($previous in $oilActions.SelectNodes('TriggerActionInfo[name="RiftOilFollowOn"]')) {
        $null = $oilActions.RemoveChild($previous)
    }
    foreach ($blast in @(@(166, '0.22'), @(168, '0.44'))) {
        $action = $firstBlast.CloneNode($true)
        Set-Field $action 'name' 'RiftOilFollowOn'
        Set-Field $action 'timeOffset' $blast[1]
        Set-GridPoint $action.SelectSingleNode('targetPoint') $blast[0] 10
        $null = $oilActions.AppendChild($action)
    }
}
if ($CascadeProject) {
    $cascade = (Get-Content -LiteralPath $CascadeProject -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable).cascade
    $hazard = $triggers.SelectNodes('TriggerInfo')[$cascade.trigger]
    $actions = $hazard.SelectSingleNode('actions')
    $first = $actions.SelectSingleNode('TriggerActionInfo[type="Collapse"]')
    if ($null -eq $first -or $null -eq $cascade.actions) { throw 'Cascade requires a Collapse trigger and target cells.' }
    foreach ($previous in $actions.SelectNodes('TriggerActionInfo[name="CascadeFollowOn"]')) {
        $null = $actions.RemoveChild($previous)
    }
    foreach ($target in $cascade.actions) {
        $action = $first.CloneNode($true)
        Set-Field $action 'name' 'CascadeFollowOn'
        Set-Field $action 'timeOffset' $target.delay.ToString([Globalization.CultureInfo]::InvariantCulture)
        Set-GridPoint $action.SelectSingleNode('targetPoint') $target.x $target.y
        $null = $actions.AppendChild($action)
    }
}
$map = $triggers.ParentNode
Set-Field $map 'onlyTriggersCanWinLevel' 'true'

$xmlStream = [IO.MemoryStream]::new()
$document.Save($xmlStream)
$xmlBytes = $xmlStream.ToArray()
$compressed = $codec.GetMethod('Compress').Invoke($null, (,$xmlBytes))
$roundtrip = $codec.GetMethod('Decompress').Invoke($null, (,$compressed))
if (-not [Linq.Enumerable]::SequenceEqual([byte[]]$xmlBytes, [byte[]]$roundtrip)) { throw 'BFC payload round-trip failed.' }

$newBytes = [byte[]]::new($blobOffset + $compressed.Length)
[Array]::Copy($original, 0, $newBytes, 0, $blobOffset)
[Array]::Copy($compressed, 0, $newBytes, $blobOffset, $compressed.Length)
$temporary = $campaignFile.FullName + '.boss-tmp'
[IO.File]::WriteAllBytes($temporary, $newBytes)
[IO.File]::Move($temporary, $campaignFile.FullName, $true)
[pscustomobject]@{ Path = $campaignFile.FullName; Bytes = $newBytes.Length; TriggerCount = $triggers.ChildNodes.Count; BossTag = $bossTag }
