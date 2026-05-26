$baseUrl = "http://127.0.0.1:8003"
$sinceDays = 365

Write-Host "Obteniendo empresas/workspaces..." -ForegroundColor Cyan
$workspaces = Invoke-RestMethod -Uri "$baseUrl/workspaces" -Method GET

Write-Host "Total de empresas encontradas: $($workspaces.Count)" -ForegroundColor Green

foreach ($workspace in $workspaces) {
    $workspaceId = $workspace.workspace_id
    $workspaceName = $workspace.workspace_name

    Write-Host ""
    Write-Host "========================================" -ForegroundColor DarkGray
    Write-Host "Generando FAQs para: $workspaceName ($workspaceId)" -ForegroundColor Yellow
    Write-Host "Ventana: ultimos $sinceDays dias" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor DarkGray

    $body = @{
        since_days = $sinceDays
        workspace_id = $workspaceId
        agent_id = $null
        limit = $null
    } | ConvertTo-Json

    try {
        $result = Invoke-RestMethod `
            -Uri "$baseUrl/suggest" `
            -Method POST `
            -ContentType "application/json" `
            -Body $body

        Write-Host "OK: $workspaceName" -ForegroundColor Green
        Write-Host "Sugerencias generadas: $($result.cluster_count)"
        Write-Host "Ejemplos analizados: $($result.total_examples)"
    }
    catch {
        Write-Host "ERROR en $workspaceName ($workspaceId)" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Proceso terminado." -ForegroundColor Cyan