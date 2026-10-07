'use strict';

const { randomUUID } = require('node:crypto');

function createTaskCoordinator({ runCommand, onState, persistTask }) {
  let activeTask = null;
  let persistence = Promise.resolve();
  let lastProgressPersistedAt = 0;

  function snapshot(task = activeTask) {
    if (!task) return null;
    return {
      taskId: task.taskId,
      kind: task.kind,
      state: task.state,
      startedAt: task.startedAt,
      progress: task.progress || null,
    };
  }

  function persist(task, { error = null, force = false, required = false } = {}) {
    const now = Date.now();
    if (!force && now - lastProgressPersistedAt < 1000) return persistence;
    lastProgressPersistedAt = now;
    const payload = { ...snapshot(task), error };
    const attempt = persistence
      .catch(() => undefined)
      .then(() => persistTask(payload));
    persistence = attempt.catch(() => undefined);
    return required ? attempt : persistence;
  }

  function broadcast(task = activeTask) {
    onState(snapshot(task));
  }

  function begin(kind) {
    if (activeTask) {
      throw new Error(`当前正在执行“${activeTask.kind}”，请等待完成或先停止`);
    }
    let finish;
    const done = new Promise((resolve) => {
      finish = resolve;
    });
    activeTask = {
      taskId: randomUUID(),
      kind,
      state: 'running',
      startedAt: new Date().toISOString(),
      child: null,
      progress: null,
      done,
      finish,
    };
    broadcast();
    activeTask.ready = persist(activeTask, { force: true, required: true });
    return activeTask;
  }

  async function finish(task, error = null) {
    if (activeTask !== task) return;
    if (task.state === 'stopping') task.state = 'interrupted';
    else task.state = error ? 'failed' : 'finished';
    task.child = null;
    let persistenceError = null;
    try {
      await persist(task, {
        error: error ? String(error.message || error) : null,
        force: true,
        required: true,
      });
    } catch (persistError) {
      persistenceError = persistError;
    } finally {
      task.finish();
      activeTask = null;
      broadcast(null);
    }
    if (persistenceError && !error) throw persistenceError;
  }

  function runForTask(task, args, webContents) {
    return runCommand(args, webContents, {
      taskId: task.taskId,
      taskKind: task.kind,
      onSpawn(child) {
        task.child = child;
        if (task.state === 'stopping' && child.exitCode === null) child.kill('SIGTERM');
      },
      onProgress(progress) {
        task.progress = progress;
        broadcast(task);
        persist(task);
      },
    });
  }

  async function run(kind, args, webContents) {
    const task = begin(kind);
    let failure = null;
    try {
      await task.ready;
      return await runForTask(task, args, webContents);
    } catch (error) {
      failure = error;
      throw error;
    } finally {
      await finish(task, failure);
    }
  }

  async function withTask(kind, operation) {
    const task = begin(kind);
    let failure = null;
    try {
      await task.ready;
      return await operation(task);
    } catch (error) {
      failure = error;
      throw error;
    } finally {
      await finish(task, failure);
    }
  }

  async function stop({ wait = false, timeoutMs = 15000 } = {}) {
    const task = activeTask;
    if (!task) return { ok: true, stopped: false };
    if (task.state !== 'stopping') {
      task.state = 'stopping';
      broadcast(task);
      persist(task, { force: true });
      if (task.child && !task.child.killed) task.child.kill('SIGTERM');
    }
    if (!wait) return { ok: true, stopped: true, task: snapshot(task) };
    let timeout;
    await Promise.race([
      task.done,
      new Promise((resolve) => {
        timeout = setTimeout(() => {
          if (activeTask === task && task.child && task.child.exitCode === null) {
            task.child.kill('SIGKILL');
          }
          resolve();
        }, timeoutMs);
      }),
    ]);
    if (timeout) clearTimeout(timeout);
    await persistence;
    return { ok: true, stopped: true, task: snapshot(task) };
  }

  return {
    hasActive: () => Boolean(activeTask),
    run,
    runForTask,
    snapshot,
    stop,
    withTask,
  };
}

module.exports = { createTaskCoordinator };
