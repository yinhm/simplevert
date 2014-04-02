DROP DATABASE IF EXISTS simplevert;
DROP DATABASE IF EXISTS simplevert_testing;
CREATE USER simplevert WITH PASSWORD 'testing';
CREATE DATABASE simplevert;
GRANT ALL PRIVILEGES ON DATABASE simplevert to simplevert;
-- Create a testing database to be different than dev
CREATE DATABASE simplevert_testing;
GRANT ALL PRIVILEGES ON DATABASE simplevert_testing to simplevert;
\c simplecoin
CREATE EXTENSION hstore;
\c simplevert_testing
CREATE EXTENSION hstore;
