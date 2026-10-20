use crate::engine::Worker;

pub struct Scheduler {
    pub worker: Worker,
}

impl Scheduler {
    pub fn new() -> Self {
        Scheduler {
            worker: Worker::new("rust_worker"),
        }
    }

    pub fn schedule_task(&self, id: u32) -> bool {
        self.worker.execute_task(id)
    }
}

pub fn run_scheduler() -> bool {
    let sched = Scheduler::new();
    sched.schedule_task(42)
}
