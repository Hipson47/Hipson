"""Start a real live runner holding the parser scope before the agent arrives."""


def setup(fixture):
    task = fixture.open_task("Concurrent parser work")
    holder = fixture.start_holder(task)
    return {"task": task, "holder": holder}
