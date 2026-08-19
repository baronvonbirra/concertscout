-- Migration script to add share tracking and lift metric columns to band_analytics_summary

ALTER TABLE band_analytics_summary
    ADD COLUMN IF NOT EXISTS total_shares INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS was_shared BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS last_shared_week VARCHAR,
    ADD COLUMN IF NOT EXISTS listener_count_at_share INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS listener_count_1week_after_share INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS share_lift_pct DECIMAL(10,2) DEFAULT 0.00,
    ADD COLUMN IF NOT EXISTS share_lift_absolute INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS avg_growth_after_share_pct DECIMAL(10,2) DEFAULT 0.00;
