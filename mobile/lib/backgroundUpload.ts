import NetInfo from "@react-native-community/netinfo";
import { requireOptionalNativeModule } from "expo";
import type * as BackgroundTaskModule from "expo-background-task";
import type * as TaskManagerModule from "expo-task-manager";

import { pingApi } from "./api";
import { ACCESS_TOKEN_KEY } from "./config";
import { BACKGROUND_STEADY_CHECK_MS, backgroundNetDecision } from "./connectivity";
import { countLotsNeedingWebsiteCheck } from "./queue/db";
import { runSyncPass } from "./queue/syncRunner";
import { secureGet } from "./secureStorage";
import { sharedSyncGate } from "./syncGate";
import { uploadPause } from "./uploadPause";
import { getUploadOnWifiOnly } from "./uploadSettings";

export const BACKGROUND_UPLOAD_TASK = "estate-ez-background-upload";
/** The OS runs the task about this often at most (it decides the real schedule). */
const BACKGROUND_INTERVAL_MINUTES = 15;
/** Stop starting new files after this; Android gives a worker ~10 min, iOS less and variable. */
const BACKGROUND_BUDGET_MS = 4 * 60_000;

type Modules = { TaskManager: typeof TaskManagerModule; BackgroundTask: typeof BackgroundTaskModule };

/**
 * Binaries built before these native modules were added still get this JS over the air.
 * Loading the JS packages there throws at import, so check the native side first.
 */
function loadModules(): Modules | null {
  if (!requireOptionalNativeModule("ExpoTaskManager") || !requireOptionalNativeModule("ExpoBackgroundTask")) {
    return null;
  }
  try {
    return {
      TaskManager: require("expo-task-manager") as typeof TaskManagerModule,
      BackgroundTask: require("expo-background-task") as typeof BackgroundTaskModule
    };
  } catch {
    return null;
  }
}

const modules = loadModules();

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export type BackgroundRunOutcome =
  | "uploaded"
  | "nothing"
  | "no_token"
  | "offline"
  | "wifi_required"
  | "unsteady"
  | "unreachable"
  | "busy"
  | "error";

/** Headless Sync when the OS wakes the app. Same pipeline as the app, with a time budget. */
export async function runBackgroundUpload(): Promise<BackgroundRunOutcome> {
  const token = await secureGet(ACCESS_TOKEN_KEY).catch(() => null);
  if (!token) return "no_token";
  if (!(await countLotsNeedingWebsiteCheck().catch(() => 0))) return "nothing";
  const wifiOnly = await getUploadOnWifiOnly();
  const first = await NetInfo.fetch();
  if (backgroundNetDecision(first, first, wifiOnly) === "ok") await sleep(BACKGROUND_STEADY_CHECK_MS);
  const net = backgroundNetDecision(first, await NetInfo.fetch(), wifiOnly);
  if (net !== "ok") return net;
  if (!(await pingApi())) return "unreachable";
  if (!sharedSyncGate.begin({})) return "busy";

  const budget = setTimeout(() => uploadPause.pause("time_budget"), BACKGROUND_BUDGET_MS);
  let expiry: { remove: () => void } | null = null;
  try {
    expiry = modules?.BackgroundTask.addExpirationListener(() => uploadPause.pause("time_budget")) ?? null;
  } catch {
    expiry = null;
  }
  try {
    const result = await runSyncPass(token, { manual: false });
    return result && (result.uploaded || result.archived || result.reconciled) ? "uploaded" : "nothing";
  } catch {
    return "error";
  } finally {
    clearTimeout(budget);
    expiry?.remove();
    sharedSyncGate.finish();
  }
}

if (modules) {
  const { TaskManager, BackgroundTask } = modules;
  if (!TaskManager.isTaskDefined(BACKGROUND_UPLOAD_TASK)) {
    TaskManager.defineTask(BACKGROUND_UPLOAD_TASK, async () => {
      const outcome = await runBackgroundUpload();
      return outcome === "error" ? BackgroundTask.BackgroundTaskResult.Failed : BackgroundTask.BackgroundTaskResult.Success;
    });
  }
}

export function backgroundUploadSupported(): boolean {
  return modules != null;
}

/** Register while signed in (lots may be waiting); unregister on sign-out. Never throws. */
export async function setBackgroundUploadEnabled(on: boolean): Promise<void> {
  if (!modules) return;
  const { TaskManager, BackgroundTask } = modules;
  try {
    if (on) {
      if ((await BackgroundTask.getStatusAsync()) !== BackgroundTask.BackgroundTaskStatus.Available) return;
      await BackgroundTask.registerTaskAsync(BACKGROUND_UPLOAD_TASK, {
        minimumInterval: BACKGROUND_INTERVAL_MINUTES
      });
    } else if (await TaskManager.isTaskRegisteredAsync(BACKGROUND_UPLOAD_TASK)) {
      await BackgroundTask.unregisterTaskAsync(BACKGROUND_UPLOAD_TASK);
    }
  } catch {
    /* background upload is a bonus — foreground Sync still runs */
  }
}
