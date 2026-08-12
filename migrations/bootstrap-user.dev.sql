-- migrations/bootstrap-user.dev.sql
-- Dev copy — the VM path is scripts/boot.sh's heredoc with the real secret;
-- keep the SQL bodies in sync.

CREATE USER IF NOT EXISTS wikistream IDENTIFIED WITH plaintext_password BY 'wikistream_dev_password' HOST ANY;
GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE ON default.* TO wikistream;
ALTER USER IF EXISTS wikistream IDENTIFIED WITH plaintext_password BY 'wikistream_dev_password' HOST ANY;
