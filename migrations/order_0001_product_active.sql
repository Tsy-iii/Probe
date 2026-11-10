-- Additive migration: old products remain active; no orders, events or stock are reset.
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
