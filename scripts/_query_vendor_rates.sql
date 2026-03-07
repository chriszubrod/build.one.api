SELECT
    v.Name AS VendorName,
    cl.HourlyRate,
    cl.Markup,
    cl.WorkDate
FROM dbo.ContractLabor cl
JOIN dbo.Vendor v ON v.Id = cl.VendorId
WHERE cl.HourlyRate IS NOT NULL
  AND cl.WorkDate = (
      SELECT MAX(cl2.WorkDate)
      FROM dbo.ContractLabor cl2
      WHERE cl2.VendorId = cl.VendorId
        AND cl2.HourlyRate IS NOT NULL
  )
GROUP BY v.Name, cl.HourlyRate, cl.Markup, cl.WorkDate
ORDER BY v.Name
