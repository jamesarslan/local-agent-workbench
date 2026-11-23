# Windows PowerShell 5.1 port of the loopback sweep in endpoints.py (_http, _identify,
# _models, _protocols, _probe_base, _sweep). Workbench prepends $Table (the ports, the
# LM Studio control ports, the server kinds and the identity checks as one JSON array)
# and the ToJson writer from remote_win.py.
# It prints one JSON line with the same shape as the Python sweep. Nothing here leaves
# 127.0.0.1.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Web.Extensions
[System.Net.ServicePointManager]::Expect100Continue = $false
$script:JS = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$script:JS.MaxJsonLength = [int]::MaxValue
$script:JS.RecursionLimit = 100
$script:Limit = 2097152

function IsDict($v) { return ($v -is [System.Collections.IDictionary]) }
function IsList($v) { return ($v -is [array]) }
function IsWhole($v) { return ($v -is [int] -or $v -is [long]) }
function IsNumber($v) { return ($v -is [bool] -or $v -is [int] -or $v -is [long] -or $v -is [decimal] -or $v -is [double]) }

function ParseJson([string]$text) {
    try { $v = $script:JS.DeserializeObject($text) } catch { return $null }
    if ($v -is [array]) { return , $v }
    return $v
}

function DictGet($d, [string]$k) {
    if ((IsDict $d) -and $d.ContainsKey($k)) {
        $v = $d[$k]
        if ($v -is [array]) { return , $v }
        return $v
    }
    return $null
}

# Python's == for the JSON values the identity table compares.
function PyEq($a, $b) {
    if ($null -eq $a -or $null -eq $b) { return ($null -eq $a -and $null -eq $b) }
    if ((IsNumber $a) -and (IsNumber $b)) { return ([decimal]$a -eq [decimal]$b) }
    if ($a -is [string] -and $b -is [string]) { return [string]::Equals($a, $b) }
    return $false
}

function PyStr($v) {
    if ($null -eq $v) { return 'None' }
    if ($v -is [bool]) { if ($v) { return 'True' } else { return 'False' } }
    return [string]$v
}

function Dig($doc, $keys) {
    foreach ($k in $keys) {
        if (IsDict $doc) { $doc = DictGet $doc ([string]$k) } else { $doc = $null }
    }
    if ($doc -is [array]) { return , $doc }
    return $doc
}

function Http([string]$method, [string]$url, $headers, $body, [double]$timeout) {
    $fail = @{ code = 0; head = @{}; body = '' }
    $resp = $null
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.Method = $method
        $req.Proxy = $null
        $req.AllowAutoRedirect = $false
        $req.KeepAlive = $false
        $ms = [int][math]::Max(1, $timeout * 1000)
        $req.Timeout = $ms
        $req.ReadWriteTimeout = $ms
        if ($null -ne $headers) {
            foreach ($k in @($headers.Keys)) {
                if ($k -eq 'Content-Type') { $req.ContentType = $headers[$k] } else { $req.Headers.Add($k, $headers[$k]) }
            }
        }
        if ($null -ne $body) {
            $bytes = [Text.Encoding]::UTF8.GetBytes([string]$body)
            $req.ContentLength = $bytes.Length
            $out = $req.GetRequestStream()
            $out.Write($bytes, 0, $bytes.Length)
            $out.Close()
        }
        try { $resp = $req.GetResponse() }
        catch [System.Net.WebException] {
            $resp = $_.Exception.Response
            if ($null -eq $resp) { return $fail }
        }
        $head = @{}
        foreach ($k in $resp.Headers.AllKeys) { $head[$k.ToLowerInvariant()] = $resp.Headers[$k] }
        $stream = $resp.GetResponseStream()
        $buf = New-Object byte[] 65536
        $kept = New-Object System.IO.MemoryStream
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while ($kept.Length -lt $script:Limit) {
            $n = $stream.Read($buf, 0, $buf.Length)
            if ($n -le 0) { break }
            if ($watch.Elapsed.TotalSeconds -gt $timeout) { return $fail }
            $kept.Write($buf, 0, $n)
        }
        $data = $kept.ToArray()
        $size = [math]::Min($data.Length, $script:Limit)
        return @{ code = [int]$resp.StatusCode; head = $head; body = [Text.Encoding]::UTF8.GetString($data, 0, $size) }
    }
    catch { return $fail }
    finally { if ($null -ne $resp) { $resp.Close() } }
}

function Items($doc) {
    if (IsDict $doc) {
        if ($doc.ContainsKey('data')) { $rows = $doc['data'] } else { $rows = DictGet $doc 'models' }
        if ($null -eq $rows -and (PyEq (DictGet $doc 'object') 'list')) { $rows = @() }
    }
    else { $rows = $doc }
    if (-not (IsList $rows)) { return $null }
    $out = New-Object System.Collections.ArrayList
    foreach ($r in $rows) {
        if (-not (IsDict $r)) { continue }
        if ($r.ContainsKey('id')) { $id = $r['id'] } else { $id = DictGet $r 'name' }
        if ($id -is [string]) { [void]$out.Add($r) }
    }
    return , $out
}

function ItemId($item) {
    if ($item.ContainsKey('id')) { return $item['id'] }
    return (DictGet $item 'name')
}

function Window($item) {
    foreach ($k in @('context_length', 'context_window', 'max_model_len', 'max_input_tokens')) {
        $v = DictGet $item $k
        if ((IsWhole $v) -and $v -gt 0) { return $v }
    }
    return $null
}

function Identify($root, $status, $head, $items, $kinds, $identity, $timeout) {
    $server = ([string]$head['server']).ToLowerInvariant()
    $realm = ([string]$head['www-authenticate']).ToLowerInvariant()
    $owners = New-Object 'System.Collections.Generic.HashSet[string]'
    if ($null -ne $items) { foreach ($i in $items) { [void]$owners.Add((PyStr (DictGet $i 'owned_by'))) } }
    foreach ($name in $kinds.Keys) {
        $marks = DictGet $kinds[$name] 'identity'
        foreach ($s in (DictGet $marks 'server')) { if ($server.Contains([string]$s)) { return $name } }
        foreach ($o in (DictGet $marks 'owned_by')) { if ($owners.Contains([string]$o)) { return $name } }
        foreach ($r in (DictGet $marks 'realm')) { if ($realm.Contains([string]$r)) { return $name } }
    }
    $seen = New-Object 'System.Collections.Generic.Dictionary[string,object]'
    foreach ($row in $identity) {
        $name = $row[0]; $path = [string]$row[1]; $test = $row[2]; $keys = $row[3]; $want = $row[4]
        if (-not $seen.ContainsKey($path)) { $seen[$path] = Http 'GET' ($root + $path) $null $null $timeout }
        $got = $seen[$path]
        if ($got.code -ne 200) { continue }
        $doc = ParseJson $got.body
        $text = $got.body
        $ok = $false
        if ($test -ceq 'eq') { $ok = PyEq (Dig $doc $keys) $want }
        elseif ($test -ceq 'has') {
            $ok = IsDict $doc
            if ($ok) { foreach ($k in $keys) { if (-not $doc.ContainsKey([string]$k)) { $ok = $false } } }
        }
        elseif ($test -ceq 'text') { $ok = $text.Contains([string]$want) }
        elseif ($test -ceq 'text_is') { $ok = [string]::Equals($text.Trim(), [string]$want) }
        elseif ($test -ceq 'server') { $ok = ([string]$got.head['server']).ToLowerInvariant().Contains([string]$want) }
        if ($ok) { return $name }
    }
    if ($null -eq $items) { return $null }
    $health = $null
    if ($seen.ContainsKey('/health')) { $health = ParseJson $seen['/health'].body }
    $plain = $items.Count -gt 0
    foreach ($i in $items) { if ($i.ContainsKey('owned_by')) { $plain = $false } }
    if ($plain -and (IsDict $health) -and (PyEq (DictGet $health 'status') 'ok')) { return 'mlx-lm' }
    return 'generic'
}

function Models($root, $kind, $items, $kinds, $headers, $timeout) {
    $plan = DictGet $kinds[$kind] 'context_source'
    $sizes = New-Object 'System.Collections.Generic.Dictionary[string,object]'
    $loaded = $null
    $skip = New-Object 'System.Collections.Generic.HashSet[string]'
    $shared = $null
    if ($plan -ceq 'ollama') {
        $doc = ParseJson (Http 'GET' ($root + '/api/ps') $headers $null $timeout).body
        $rows = DictGet $doc 'models'
        if (IsList $rows) {
            $loaded = New-Object 'System.Collections.Generic.HashSet[string]'
            foreach ($row in $rows) {
                if (-not (IsDict $row)) { continue }
                foreach ($key in @((DictGet $row 'name'), (DictGet $row 'model'))) {
                    if ($key -is [string]) { [void]$loaded.Add($key); $sizes[$key] = Window $row }
                }
            }
        }
    }
    elseif ($plan -ceq 'lmstudio') {
        $doc = ParseJson (Http 'GET' ($root + '/api/v1/models') $headers $null $timeout).body
        $rows = DictGet $doc 'models'
        if (IsList $rows) {
            $loaded = New-Object 'System.Collections.Generic.HashSet[string]'
            foreach ($row in $rows) {
                if (-not (IsDict $row)) { continue }
                $name = DictGet $row 'key'
                $type = DictGet $row 'type'
                $embeds = (PyEq $type 'embedding') -or (PyEq $type 'embeddings')
                if ($embeds -and $name -is [string]) { [void]$skip.Add($name) }
                $running = DictGet $row 'loaded_instances'
                if (-not (IsList $running)) { continue }
                foreach ($inst in $running) {
                    if (-not (IsDict $inst)) { continue }
                    $config = DictGet $inst 'config'
                    $size = $null
                    if (IsDict $config) { $size = Window $config }
                    foreach ($key in @($name, (DictGet $inst 'id'))) {
                        if ($key -is [string]) { [void]$loaded.Add($key); $sizes[$key] = $size }
                    }
                }
            }
        }
    }
    elseif ($plan -ceq 'props' -and $items.Count -eq 1) {
        $doc = ParseJson (Http 'GET' ($root + '/props') $headers $null $timeout).body
        $shared = Window @{ context_length = (Dig $doc @('default_generation_settings', 'n_ctx')) }
        $loaded = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($i in $items) { [void]$loaded.Add((ItemId $i)) }
    }
    elseif ($plan -ceq 'kobold') {
        $doc = ParseJson (Http 'GET' ($root + '/api/extra/true_max_context_length') $headers $null $timeout).body
        $shared = Window @{ context_length = (Dig $doc @('value')) }
    }
    $out = New-Object System.Collections.ArrayList
    foreach ($item in $items) {
        $ident = ItemId $item
        if ($skip.Contains($ident)) { continue }
        $size = $null
        if ($sizes.ContainsKey($ident)) { $size = $sizes[$ident] }
        if (-not $size) { $size = $shared }
        if (-not $size -and $plan -ceq 'item') { $size = Window $item }
        $state = $null
        if ($null -ne $loaded) { $state = $loaded.Contains($ident) }
        [void]$out.Add([ordered]@{ id = $ident; context = $size; loaded = $state })
        if ($out.Count -ge 500) { break }
    }
    return , $out
}

function Protocols([string]$base, [string]$prefix, $headers, $timeout) {
    if ($base.EndsWith('/v1')) { $stem = $base.Substring(0, $base.Length - 3) } else { $stem = $base }
    $sent = @{}
    foreach ($k in @($headers.Keys)) { $sent[$k] = $headers[$k] }
    $sent['Content-Type'] = 'application/json'
    $out = [ordered]@{}
    foreach ($pair in @(@('chat', ($base + '/chat/completions')), @('responses', ($base + '/responses')), @('messages', ($stem + $prefix + '/v1/messages')))) {
        $got = Http 'POST' $pair[1] $sent '{}' $timeout
        $code = $got.code
        $text = $got.body.ToLowerInvariant()
        if ($code -eq 405 -or $code -eq 501 -or ($code -eq 404 -and -not $text.Contains('model')) -or ($code -eq 200 -and $text.Contains('unexpected endpoint'))) {
            $out[$pair[0]] = $false
        }
        elseif ($code -eq 200 -or $code -eq 400 -or $code -eq 422) { $out[$pair[0]] = $true }
        else { $out[$pair[0]] = $null }
    }
    return $out
}

function ProbeBase([string]$base, $kinds, $identity, $headers, $timeout) {
    $root = ($base.Split('/') | Select-Object -First 3) -join '/'
    $got = Http 'GET' ($base + '/models') $headers $null $timeout
    if (-not $got.code -or ([string]$got.head['server']).ToLowerInvariant().Contains('airtunes')) { return $null }
    $items = $null
    if ($got.code -eq 200) { $items = Items (ParseJson $got.body) }
    $locked = ($got.code -eq 401 -or $got.code -eq 403)
    if ($null -eq $items -and -not $locked) { return $null }
    $kind = Identify $root $got.code $got.head $items $kinds $identity $timeout
    if (-not $kind) { $kind = 'generic' }
    $record = [ordered]@{
        base      = $base
        kind      = $kind
        auth      = 'none'
        models    = @()
        protocols = [ordered]@{ chat = $null; responses = $null; messages = $null }
    }
    if ($locked) {
        if ($headers.Count) { $record.auth = 'rejected' } else { $record.auth = 'required' }
        return $record
    }
    if ($headers.Count) { $record.auth = 'accepted' }
    $record.models = Models $root $kind $items $kinds $headers $timeout
    $record.protocols = Protocols $base ([string](DictGet $kinds[$kind] 'anthropic_prefix')) $headers $timeout
    return $record
}

function Listening([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $wait = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $wait.AsyncWaitHandle.WaitOne(250)) { return $false }
        $client.EndConnect($wait)
        return $true
    }
    catch { return $false }
    finally { $client.Close() }
}

function Sweep($ports, $control, $kinds, $identity, $timeout) {
    $found = New-Object System.Collections.ArrayList
    foreach ($port in $ports) {
        if (-not (Listening $port)) { continue }
        $record = ProbeBase ('http://127.0.0.1:' + $port + '/v1') $kinds $identity @{} $timeout
        if ($null -ne $record -and ($record.kind -cne 'generic' -or $record.auth -ceq 'none')) {
            $record.port = [int]$port
            [void]$found.Add($record)
        }
    }
    $hints = New-Object System.Collections.ArrayList
    $studio = $false
    foreach ($r in $found) { if ($r.kind -ceq 'lmstudio') { $studio = $true } }
    if (-not $studio) {
        foreach ($port in $control) {
            if (-not (Listening $port)) { continue }
            $doc = ParseJson (Http 'GET' ('http://127.0.0.1:' + $port + '/lmstudio-greeting') @{} $null $timeout).body
            if ((Dig $doc @('lmstudio')) -is [bool] -and (Dig $doc @('lmstudio'))) {
                [void]$hints.Add('lmstudio-server-off')
                break
            }
        }
    }
    return [ordered]@{ v = 1; endpoints = $found; hints = $hints }
}

try {
    $t = $script:JS.DeserializeObject($Table)
    $result = Sweep $t[0] $t[1] $t[2] $t[3] 1.0
    [Console]::Out.WriteLine((ToJson $result))
}
catch {
    [Console]::Error.WriteLine('The model server scan failed on this machine: ' + $_.Exception.Message)
    exit 1
}
