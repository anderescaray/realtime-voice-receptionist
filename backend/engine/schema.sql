PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE site (site_id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL,
                   phone TEXT NOT NULL, timezone TEXT NOT NULL);
CREATE TABLE practitioners (practitioner_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                   role TEXT NOT NULL CHECK (role IN ('dentist','hygienist')),
                   languages TEXT NOT NULL);
CREATE TABLE treatments (treatment_id TEXT PRIMARY KEY,
                   name_es TEXT NOT NULL, name_en TEXT NOT NULL,
                   minutes INTEGER NOT NULL CHECK (minutes > 0 AND minutes % 15 = 0),
                   price_eur INTEGER);
CREATE TABLE practitioner_treatments (practitioner_id TEXT REFERENCES practitioners,
                   treatment_id TEXT REFERENCES treatments, PRIMARY KEY (practitioner_id, treatment_id));
CREATE TABLE working_hours (practitioner_id TEXT REFERENCES practitioners,
                   weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                   start_local TEXT NOT NULL, end_local TEXT NOT NULL);
CREATE TABLE closures (date TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE absences (practitioner_id TEXT REFERENCES practitioners,
                   start_date TEXT NOT NULL, end_date TEXT NOT NULL, reason TEXT NOT NULL,
                   start_local TEXT, end_local TEXT,
                   CHECK ((start_local IS NULL) = (end_local IS NULL)),
                   CHECK (start_local IS NULL OR start_local < end_local));
CREATE TABLE patients (patient_id TEXT PRIMARY KEY, first_name TEXT NOT NULL,
                   last_name1 TEXT NOT NULL, last_name2 TEXT, dob TEXT NOT NULL,
                   phone TEXT NOT NULL,
                   language TEXT NOT NULL DEFAULT 'es', created_by_call TEXT);
CREATE TABLE appointments (appointment_id TEXT PRIMARY KEY,
                   patient_id TEXT NOT NULL REFERENCES patients,
                   practitioner_id TEXT NOT NULL REFERENCES practitioners,
                   treatment_id TEXT NOT NULL REFERENCES treatments,
                   start_utc TEXT NOT NULL, end_utc TEXT NOT NULL CHECK (end_utc > start_utc),
                   status TEXT NOT NULL CHECK (status IN ('booked','cancelled')),
                   same_day_urgent INTEGER NOT NULL DEFAULT 0 CHECK (same_day_urgent IN (0,1)),
                   created_by_call TEXT);
CREATE TABLE changes (change_id INTEGER PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
                   call_id TEXT NOT NULL, action TEXT NOT NULL
                   CHECK (action IN ('register','book','move','cancel','demo_reset')),
                   details TEXT NOT NULL, saved_utc TEXT NOT NULL);
CREATE TABLE messages (message_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, name TEXT NOT NULL,
                   phone TEXT NOT NULL, text TEXT NOT NULL,
                   urgent INTEGER NOT NULL DEFAULT 0 CHECK (urgent IN (0,1)), saved_utc TEXT NOT NULL);
CREATE TABLE review_links (token_sha256 TEXT PRIMARY KEY, call_id TEXT NOT NULL UNIQUE,
                   expires_utc TEXT NOT NULL);

CREATE INDEX idx_appointments_practitioner_start ON appointments (practitioner_id, start_utc);
CREATE INDEX idx_appointments_patient_start ON appointments (patient_id, start_utc);

CREATE TRIGGER appointments_no_overlap_insert
BEFORE INSERT ON appointments
WHEN NEW.status = 'booked'
BEGIN
  SELECT RAISE(ABORT, 'overlap')
  WHERE EXISTS (
    SELECT 1 FROM appointments a
    WHERE a.status = 'booked'
      AND (
        (a.practitioner_id = NEW.practitioner_id)
        OR (a.patient_id = NEW.patient_id)
      )
      AND a.start_utc < NEW.end_utc
      AND a.end_utc > NEW.start_utc
  );
END;

CREATE TRIGGER appointments_no_overlap_update
BEFORE UPDATE OF start_utc, end_utc, practitioner_id, status ON appointments
WHEN NEW.status = 'booked'
BEGIN
  SELECT RAISE(ABORT, 'overlap')
  WHERE EXISTS (
    SELECT 1 FROM appointments a
    WHERE a.status = 'booked'
      AND a.appointment_id <> NEW.appointment_id
      AND (
        (a.practitioner_id = NEW.practitioner_id)
        OR (a.patient_id = NEW.patient_id)
      )
      AND a.start_utc < NEW.end_utc
      AND a.end_utc > NEW.start_utc
  );
END;
