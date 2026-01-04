# Azure App Service Log Queries

## Correct Log Queries for Intuit OAuth Debugging

### Option 1: Application Insights (if enabled)
```kusto
traces
| where message contains "INTUIT" or message contains "redirect_uri"
| order by timestamp desc
| take 50
| project timestamp, message, severityLevel
```

### Option 2: App Service Logs (Application Logging)
```kusto
AppServiceConsoleLogs
| where LogMessage contains "INTUIT" or LogMessage contains "redirect_uri"
| order by TimeGenerated desc
| take 50
```

### Option 3: All Log Types
```kusto
union AppServiceHTTPLogs, AppServiceConsoleLogs, AppServiceAppLogs
| where LogMessage contains "INTUIT" or LogMessage contains "redirect_uri" or Message contains "INTUIT"
| order by TimeGenerated desc
| take 50
```

### Option 4: Simple Text Search (Most Reliable)
```kusto
search "INTUIT" or "redirect_uri"
| order by TimeGenerated desc
| take 50
```

## Alternative: Use Log Stream (Easiest)

1. Azure Portal → Your App Service
2. **Monitoring** → **Log stream**
3. Click **Start**
4. Try connecting the integration
5. Watch logs appear in real-time

## Alternative: Use Kudu/SCM Site

1. Go to: `https://buildone-esgaducjg4d3eucf.scm.azurewebsites.net`
2. **Debug console** → **CMD** or **PowerShell**
3. Navigate to: `LogFiles\Application`
4. View recent log files

## Alternative: Download Logs

1. Azure Portal → Your App Service
2. **Monitoring** → **Logs**
3. Click **Download** to get log files
4. Search for "INTUIT" in the downloaded files

