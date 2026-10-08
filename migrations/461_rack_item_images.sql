-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Shared library of rack item images, keyed by item type so a photo of one
-- device can be reused across other devices of the same type. `kind` splits
-- the library into "device" (tech photos of the actual item) and "product"
-- (vendor-supplied images, often imported from a shop product).
CREATE TABLE IF NOT EXISTS rack_item_images (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    item_type VARCHAR(20) NOT NULL,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('device', 'product')),
    storage_name TEXT NOT NULL,
    thumbnail_name TEXT,
    content_type VARCHAR(100) NOT NULL,
    size_bytes INT NOT NULL,
    content_hash VARCHAR(64) NULL,
    caption VARCHAR(191) NULL,
    source_product_id INT NULL,
    uploaded_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, item_type, kind, content_hash),
    FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    FOREIGN KEY (source_product_id) REFERENCES shop_products (id) ON DELETE SET NULL,
    FOREIGN KEY (uploaded_by) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX idx_rack_item_images_lookup ON rack_item_images (company_id, item_type, kind);

-- Attachments of library images to a specific rack item, so an image can be
-- shared across items of a type while only some items display it.
CREATE TABLE IF NOT EXISTS rack_equipment_images (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    equipment_id INT NOT NULL,
    image_id INT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (equipment_id, image_id),
    FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    FOREIGN KEY (equipment_id) REFERENCES rack_equipment (id) ON DELETE CASCADE,
    FOREIGN KEY (image_id) REFERENCES rack_item_images (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX idx_rack_equipment_images_image ON rack_equipment_images (image_id);
