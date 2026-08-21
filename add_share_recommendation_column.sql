-- Migration script to add share_recommendation tracking column to weekly_submissions

ALTER TABLE weekly_submissions
    ADD COLUMN IF NOT EXISTS share_recommendation VARCHAR;
