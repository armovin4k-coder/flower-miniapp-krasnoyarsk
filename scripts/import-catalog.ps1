param(
    [string]$CatalogPath = (Join-Path $PSScriptRoot '..\catalog-import.json'),
    [string]$ApiUrl = 'http://localhost:8000'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$envPath = Join-Path $root '.env'

if (-not (Test-Path $envPath)) {
    throw 'Local .env file is missing.'
}
if (-not (Test-Path $CatalogPath)) {
    throw "Catalog file was not found: $CatalogPath"
}

$envValues = @{}
foreach ($line in Get-Content $envPath) {
    if ($line -match '^\s*([^#=]+)\s*=\s*(.*)\s*$') {
        $envValues[$matches[1]] = $matches[2]
    }
}

$adminKey = $envValues['ADMIN_API_KEY']
if ([string]::IsNullOrWhiteSpace($adminKey) -or $adminKey -like 'replace-with*') {
    throw 'Set a private ADMIN_API_KEY in .env before importing the catalog.'
}

$catalogJson = [System.IO.File]::ReadAllText($CatalogPath, [System.Text.Encoding]::UTF8)
$catalog = ConvertFrom-Json -InputObject $catalogJson
$items = @($catalog.items | ForEach-Object {
    @{
        source_id = [string]$_.source_id
        title = [string]$_.title
        description = [string]$_.description
        categories = @($_.categories | ForEach-Object { [string]$_ })
        price_rub = [int][math]::Round([double]$_.price_rub)
        image_url = [string]$_.image_url
    }
})

if ($items.Count -ne 85) {
    throw "Expected 85 catalog products; found $($items.Count). Import cancelled."
}

$body = ConvertTo-Json -InputObject @{ items = $items } -Depth 10
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
$response = Invoke-RestMethod `
    -Method Post `
    -Uri "$($ApiUrl.TrimEnd('/'))/api/admin/products/import" `
    -Headers @{ Authorization = "Bearer $adminKey" } `
    -ContentType 'application/json; charset=utf-8' `
    -Body $bodyBytes

Write-Output "Products imported: $($response.total) (new: $($response.created), updated: $($response.updated))"
Write-Output 'Stock is set to zero until actual inventory is confirmed.'
