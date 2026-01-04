# Simple Azure Log Queries

## Try These Queries (One at a time)

### Query 1: Simple Search (Usually Works)
```kusto
search "INTUIT"
| order by TimeGenerated desc
| take 50
```

### Query 2: Search with OR
```kusto
search "INTUIT" or "redirect_uri" or "OAuth"
| order by TimeGenerated desc
| take 50
```

### Query 3: Check Table Structure First
```kusto
AppServiceConsoleLogs
| take 10
```

This shows you the actual column names, then you can adjust the query.

### Query 4: HTTP Logs
```kusto
AppServiceHTTPLogs
| where CsUriStem contains "/intuit" or CsUriStem contains "/api/v1"
| order by TimeGenerated desc
| take 50
```

## EASIEST: Use Log Stream Instead

1. Azure Portal → Your App Service
2. **Monitoring** → **Log stream** (not "Logs")
3. Click **Start**
4. Try connecting integration
5. See logs in real-time - no queries needed!

