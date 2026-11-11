-- migrations/bootstrap-user.dev.sql
-- Dev copy — the VM path is scripts/boot.sh's heredoc with the real secret;
-- keep the SQL bodies in sync.

CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY 'wikistream_dev_password' HOST ANY;
ALTER USER IF EXISTS wikistream IDENTIFIED WITH plaintext_password BY 'wikistream_dev_password' HOST ANY;
REVOKE ALL PRIVILEGES ON *.* FROM wikistream;
GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE, BACKUP ON default.* TO wikistream;
-- Read-only metadata for migration checks; still no access to other databases.
GRANT SELECT ON system.tables TO wikistream;
GRANT SELECT ON system.columns TO wikistream;
GRANT SELECT ON system.databases TO wikistream;

-- Grafana's read-only identity: separate user and password, so a compromised
-- dashboard/panel cannot DDL/DROP warehouse tables with the write credentials.
CREATE USER IF NOT EXISTS wikistream_ro IDENTIFIED WITH plaintext_password BY 'wikistream_ro_dev_password' HOST ANY;
ALTER USER IF EXISTS wikistream_ro IDENTIFIED WITH plaintext_password BY 'wikistream_ro_dev_password' HOST ANY;
REVOKE ALL PRIVILEGES ON *.* FROM wikistream_ro;
GRANT SELECT ON default.* TO wikistream_ro;
GRANT SELECT ON system.tables TO wikistream_ro;
GRANT SELECT ON system.columns TO wikistream_ro;
GRANT SELECT ON system.databases TO wikistream_ro;
