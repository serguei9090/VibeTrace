pub struct Config {
    pub port: u16,
}

impl Config {
    pub fn new(port: u16) -> Self {
        Self { port }
    }

    pub fn is_valid(&self) -> bool {
        self.port > 1024
    }
}
