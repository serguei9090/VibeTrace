pub struct Worker {
    pub name: String,
}

impl Worker {
    pub fn new(name: &str) -> Self {
        Worker {
            name: name.to_string(),
        }
    }

    pub fn execute_task(&self, task_id: u32) -> bool {
        task_id > 0
    }
}
