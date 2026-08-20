\if :{?nl2sql_password}
\else
\echo 'nl2sql_password psql variable is required'
\quit
\endif

SELECT format('CREATE ROLE directdesk_readonly LOGIN PASSWORD %L', :'nl2sql_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'directdesk_readonly')
\gexec

ALTER ROLE directdesk_readonly SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE :DBNAME TO directdesk_readonly;
GRANT USAGE ON SCHEMA public TO directdesk_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO directdesk_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO directdesk_readonly;
