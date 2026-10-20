package storage

// Database handles record persistence.
type Database struct {
	driver string
}

// NewDatabase initializes a database connection handle.
func NewDatabase(driver string) *Database {
	return &Database{driver: driver}
}

// Query performs a key lookup.
func (d *Database) Query(key string) string {
	return "result_for_" + key
}
