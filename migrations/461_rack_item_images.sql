-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Shared library of rack item images, keyed by item type so a photo of one
-- device can be reused across other devices of the same type. `kind` splits
-- the library into "device" (tech photos of the actual item) and "product"
-- (vendor-supplied images, often imported from a shop product).
CREATE TABLE IF NOT EXISTS rack_item_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    item_type VARCHAR(20) NOT NULL,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('device', 'product')),
    storage_name TEXT NOT NULL,
    thumbnail_name TEXT,
    content_type VARCHAR(100) NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_hash VARCHAR(64),
    caption VARCHAR(191),
    source_product_id INTEGER,
    uploaded_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, item_type, kind, content_hash),
    FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    FOREIGN KEY (source_product_id) REFERENCES shop_products (id) ON DELETE SET NULL,
    FOREIGN KEY (uploaded_by) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_rack_item_images_lookup ON rack_item_images (company_id, item_type, kind);

-- Attachments of library images to a specific rack item, so an image can be
-- shared across items of a type while only some items display it.
CREATE TABLE IF NOT EXISTS rack_equipment_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    equipment_id INTEGER NOT NULL,
    image_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (equipment_id, image_id),
    FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    FOREIGN KEY (equipment_id) REFERENCES rack_equipment (id) ON DELETE CASCADE,
    FOREIGN KEY (image_id) REFERENCES rack_item_images (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rack_equipment_images_image ON rack_equipment_images (image_id);
