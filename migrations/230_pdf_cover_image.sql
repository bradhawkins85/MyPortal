-- Add pdf_cover_image column to site_settings for a custom PDF report cover background image.
ALTER TABLE site_settings ADD COLUMN pdf_cover_image VARCHAR(500) DEFAULT NULL;
