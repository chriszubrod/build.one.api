-- U-784a: orphaned application-lock watchdog (report mode). READ-ONLY.
-- Contract: dbo.ReadApplicationLockHolders returns ONE result set, one row per
--   granted APPLICATION lock of THIS database (resource_database_id = DB_ID()) in
--   sys.dm_tran_locks joined to its holder session. ResourceDescription is raw -
--   parse the lock name in Python (SQL Server may truncate long names). All ages
--   are computed here with GETDATE() because the session DMVs use server local time.
--   HostLastRequestSecondsAgo is 0 when ANY user session of the holder's host
--   (excluding the caller, @@SPID) has a request executing right now - a host busy
--   in a long-running request is not silent - else seconds since the later of
--   last_request_start_time / last_request_end_time over that host's sessions.
--   dbo.CountVisibleUserSessions returns ONE row: VisibleUserSessions (user sessions
--   with a host_name that the caller can see - <= 1 means only its own) and
--   CallerHostName (HOST_NAME()). Neither proc writes anything.
--   Permissions: both read OTHER sessions' DMV rows and need VIEW DATABASE STATE.
--   Prod connects as the server admin today. If that ever changes, do NOT grant
--   ##MS_ServerStateReader## or VIEW SERVER STATE to the app login, and never wrap these
--   procs in EXECUTE AS OWNER - use a dedicated principal or certificate-signed procedures.
--   Without visibility the procs return only the caller's own session.
--   No dependencies; apply any time.

CREATE OR ALTER PROCEDURE dbo.ReadApplicationLockHolders
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        l.resource_description                                   AS ResourceDescription,
        l.request_mode                                           AS RequestMode,
        s.session_id                                             AS SessionId,
        s.host_name                                              AS HostName,
        s.program_name                                           AS ProgramName,
        s.login_time                                             AS LoginTime,
        s.status                                                 AS SessionStatus,
        DATEDIFF(SECOND,
                 COALESCE(s.last_request_end_time, s.last_request_start_time, s.login_time),
                 GETDATE())                                      AS HolderIdleSeconds,
        (SELECT DATEDIFF(SECOND, MIN(at.transaction_begin_time), GETDATE())
           FROM sys.dm_tran_session_transactions st
           JOIN sys.dm_tran_active_transactions at
             ON at.transaction_id = st.transaction_id
          WHERE st.session_id = s.session_id)                    AS TransactionAgeSeconds,
        (SELECT COUNT(*)
           FROM sys.dm_tran_locks o
          WHERE o.request_session_id = s.session_id
            AND o.request_status = 'GRANT'
            AND o.resource_type NOT IN ('APPLICATION', 'DATABASE')) AS OtherLockCount,
        CASE
            WHEN s.host_name IS NULL THEN NULL
            WHEN EXISTS (SELECT 1
                           FROM sys.dm_exec_sessions h
                           JOIN sys.dm_exec_requests r ON r.session_id = h.session_id
                          WHERE h.is_user_process = 1
                            AND h.host_name = s.host_name
                            AND h.session_id <> @@SPID) THEN 0
            ELSE (SELECT DATEDIFF(SECOND,
                                  MAX(CASE WHEN h.last_request_end_time > h.last_request_start_time
                                           THEN h.last_request_end_time
                                           ELSE h.last_request_start_time END),
                                  GETDATE())
                    FROM sys.dm_exec_sessions h
                   WHERE h.is_user_process = 1
                     AND h.host_name = s.host_name
                     AND h.session_id <> @@SPID)
        END                                                      AS HostLastRequestSecondsAgo
    FROM sys.dm_tran_locks l
    JOIN sys.dm_exec_sessions s
      ON s.session_id = l.request_session_id
    WHERE l.resource_type = 'APPLICATION'
      AND l.request_status = 'GRANT'
      AND l.resource_database_id = DB_ID();
END
GO

CREATE OR ALTER PROCEDURE dbo.CountVisibleUserSessions
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        COUNT(*)    AS VisibleUserSessions,
        HOST_NAME() AS CallerHostName
    FROM sys.dm_exec_sessions
    WHERE is_user_process = 1
      AND host_name IS NOT NULL;
END
GO
