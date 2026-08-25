-- Migration SQL script to add ticket_url column to tour_events table

ALTER TABLE tour_events ADD COLUMN IF NOT EXISTS ticket_url VARCHAR;
