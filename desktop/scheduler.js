'use strict';

function localDayKey(value) {
  const date = new Date(value);
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function minuteOfDay(value) {
  const [hour, minute] = String(value).split(':').map(Number);
  return hour * 60 + minute;
}

function timeInWindow(nowMinutes, start, end) {
  const startMinutes = minuteOfDay(start);
  const endMinutes = minuteOfDay(end);
  if (startMinutes <= endMinutes) {
    return nowMinutes >= startMinutes && nowMinutes <= endMinutes;
  }
  return nowMinutes >= startMinutes || nowMinutes <= endMinutes;
}

function scheduledOccurrence(plan, now) {
  const scheduleMinutes = minuteOfDay(plan.scheduleTime);
  const startMinutes = minuteOfDay(plan.windowStart);
  const endMinutes = minuteOfDay(plan.windowEnd);
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  const target = new Date(now);
  target.setHours(Math.floor(scheduleMinutes / 60), scheduleMinutes % 60, 0, 0);
  if (startMinutes > endMinutes) {
    if (scheduleMinutes >= startMinutes && nowMinutes < startMinutes) {
      target.setDate(target.getDate() - 1);
    } else if (scheduleMinutes <= endMinutes && nowMinutes >= startMinutes) {
      target.setDate(target.getDate() + 1);
    }
  }
  return target;
}

function planRunRecorded(plan, history, target) {
  const day = localDayKey(target);
  return history.some(
    (run) => run.planId === plan.id &&
      run.triggerType === 'scheduled' &&
      localDayKey(run.scheduledFor || run.startedAt) === day,
  );
}

function planIsDue(plan, history, now = new Date()) {
  if (!plan.enabled || plan.scheduleKind !== 'daily') return false;
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  if (!timeInWindow(nowMinutes, plan.windowStart, plan.windowEnd)) return false;
  const target = scheduledOccurrence(plan, now);
  return now >= target && !planRunRecorded(plan, history, target);
}

function planWasMissed(plan, history, now = new Date()) {
  if (!plan.enabled || plan.scheduleKind !== 'daily') return false;
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  if (timeInWindow(nowMinutes, plan.windowStart, plan.windowEnd)) return false;
  const target = scheduledOccurrence(plan, now);
  if (plan.updatedAt && new Date(plan.updatedAt) > target) return false;
  return now > target && !planRunRecorded(plan, history, target);
}

function createScheduler({
  runCommand,
  taskCoordinator,
  runtimeAvailable,
  getWebContents,
  onError,
}) {
  let timer = null;
  let checking = false;

  async function check() {
    if (checking || taskCoordinator.hasActive() || !runtimeAvailable()) return;
    checking = true;
    try {
      const [planResult, historyResult] = await Promise.all([
        runCommand(['scan-plan', 'list'], null),
        runCommand(['scan-plan', 'history', '--limit', '200'], null),
      ]);
      const plans = planResult.parsed?.plans || [];
      const history = historyResult.parsed?.runs || [];
      for (const plan of plans.filter((item) => planWasMissed(item, history))) {
        const scheduledFor = scheduledOccurrence(plan, new Date()).toISOString();
        const missed = await runCommand(
          ['scan-plan', 'missed', plan.id, '--scheduled-for', scheduledFor],
          null,
        );
        if (missed.parsed) {
          history.push({
            planId: plan.id,
            triggerType: 'scheduled',
            startedAt: scheduledFor,
          });
        }
      }
      const due = plans.find((plan) => planIsDue(plan, history));
      if (!due || taskCoordinator.hasActive()) return;
      const scheduledFor = scheduledOccurrence(due, new Date()).toISOString();
      taskCoordinator.run(
        '自动更新',
        [
          'scan-plan', 'run', due.id, '--trigger', 'scheduled',
          '--scheduled-for', scheduledFor,
        ],
        getWebContents(),
      ).catch((error) => onError('自动更新', error));
    } catch (error) {
      onError('自动更新调度', error);
    } finally {
      checking = false;
    }
  }

  function start() {
    if (timer) clearInterval(timer);
    timer = setInterval(check, 60 * 1000);
    timer.unref?.();
    setTimeout(check, 1500);
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  async function runAll(webContents) {
    return taskCoordinator.withTask('立即更新全部', async (task) => {
      const results = [];
      const listResult = await taskCoordinator.runForTask(
        task,
        ['scan-plan', 'list'],
        webContents,
      );
      const plans = (listResult.parsed?.plans || []).filter((plan) => plan.enabled);
      for (const plan of plans) {
        if (task.state === 'stopping') break;
        const result = await taskCoordinator.runForTask(
          task,
          ['scan-plan', 'run', plan.id, '--trigger', 'manual'],
          webContents,
        );
        results.push(result.parsed || {});
      }
      return { ok: true, results };
    });
  }

  return { check, runAll, start, stop };
}

module.exports = {
  createScheduler,
  planIsDue,
  planWasMissed,
  scheduledOccurrence,
  timeInWindow,
};
