from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor as TPE
from queue import Queue
from typing import Iterable, Callable, Generator
import multiprocessing as mp
import pickle


def iter_until_none(f: Callable, count=1) -> Generator:
    seen = 0
    while True:
        item = f()
        if item is None:
            seen += 1
            if seen == count:
                break
            else:
                continue
        yield item


class Executor(ABC):
    @abstractmethod
    def execute(self, task: Callable, upstream: Iterable) -> Generator:
        pass


class CurrentThreadExecutor(Executor):
    def execute(self, task: Callable, upstream: Iterable) -> Generator:
        for item in upstream:
            for output in task(item):
                yield output


class BackgroundThreadExecutor(Executor):
    def execute(self, task: Callable, upstream: Iterable) -> Generator:
        queue = Queue(maxsize=1)
        background = TPE(1, 'background-executor')
        background.submit(self._work, upstream, task, queue, background)

        for output in iter_until_none(queue.get):
            yield output

    @classmethod
    def _work(cls, upstream: Iterable, task: Callable, queue: Queue, executor: TPE):
        for item in upstream:
            for output in task(item):
                queue.put(output)
        queue.put(None)
        executor.shutdown(wait=False)


class ThreadPoolExecutor(Executor):
    threads: int

    def __init__(self, threads):
        self.threads = threads

    def execute(self, task: Callable, upstream: Iterable) -> Generator:
        input_queue = Queue(maxsize=self.threads)
        output_queue = Queue()

        collector = TPE(1, 'thread-pool-executor-collector')
        collector.submit(self._collect, upstream, input_queue)

        workers = TPE(self.threads, 'thread-pool-executor-worker')
        for _ in range(self.threads):
            workers.submit(self._work, input_queue, task, output_queue)

        for output in iter_until_none(output_queue.get, self.threads):
            yield output

        collector.shutdown()
        workers.shutdown()

    def _collect(self, upstream: Iterable, input_queue: Queue):
        for item in upstream:
            input_queue.put(item)
        for _ in range(self.threads):
            input_queue.put(None)

    @classmethod
    def _work(cls, input_queue: Queue, task: Callable, output_queue: Queue):
        for item in iter_until_none(input_queue.get):
            for output in task(item):
                output_queue.put(output)
        output_queue.put(None)


class MultiProcessingExecutor(Executor):
    def __init__(self, processes):
        self.processes = processes

    def execute(self, task: Callable, upstream: Iterable) -> Generator:
        manager = mp.Manager()
        manager.register('None', type(None))
        input_queue = manager.Queue(maxsize=self.processes)
        output_queue = manager.Queue()

        collector = TPE(1, 'multiprocessing-executor-collector')
        collector.submit(self._collect, upstream, input_queue)

        with mp.Pool(self.processes) as pool:
            for _ in range(self.processes):
                pool.apply_async(MultiProcessingExecutor._work, args=(input_queue, task, output_queue))
            for output in iter_until_none(output_queue.get, self.processes):
                yield pickle.loads(output)

        collector.shutdown()

    def _collect(self, upstream: Iterable, input_queue: mp.Queue):
        for item in upstream:
            input_queue.put(pickle.dumps(item))
        for _ in range(self.processes):
            input_queue.put(None)

    @classmethod
    def _work(cls, input_queue: mp.Queue, task: Callable, output_queue: mp.Queue):
        for item in iter_until_none(input_queue.get):
            for output in task(pickle.loads(item)):
                output_queue.put(pickle.dumps(output))
        output_queue.put(None)


import time
import numpy as np

def add1(n):
    time.sleep(1)
    yield n + 10


if __name__ == "__main__":
    t = ThreadPoolExecutor(5)
    print(list(t.execute(add1, np.random.randn(10, 10))))
