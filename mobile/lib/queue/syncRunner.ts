import NetInfo from "@react-native-community/netinfo";

import { classifyNet } from "../connectivity";
import { uploadPause } from "../uploadPause";
import { getUploadOnWifiOnly } from "../uploadSettings";
import { countLotsNeedingPhotoUpload, countLotsNeedingWebsiteCheck } from "./db";
import { formatSyncOutcome } from "./queueStatus";
import {
  attachPendingLotVideos,
  loadWebsiteIndex,
  sweepArchiveConfirmedDrafts,
  syncPendingLots
} from "./sync";

export type SyncResult = {
  uploaded: number;
  failed: number;
  reconciled: number;
  archived?: number;
  healthSummary?: string | null;
  paused?: boolean;
  waitingForWifi?: number;
};

export type SyncPassOptions = {
  manual?: boolean;
  /** Upload on Wi‑Fi only is on, but staff chose to send over cellular this time. */
  allowCellular?: boolean;
  onMessage?: (text: string) => void;
  onProgress?: () => void;
};

function lotsWord(n: number): string {
  return `${n} lot${n === 1 ? "" : "s"}`;
}

/**
 * One Sync: website checks, photo/video uploads, archive sweep. Shared by the app and the
 * background task (the caller holds ``sharedSyncGate``). ``null`` = did not run (offline).
 */
export async function runSyncPass(token: string, opts: SyncPassOptions = {}): Promise<SyncResult | null> {
  const say = (text: string) => opts.onMessage?.(text);
  const net = await NetInfo.fetch();
  const wifiOnly = await getUploadOnWifiOnly();
  const use = classifyNet(net, wifiOnly);
  if (use === "offline") {
    const waiting = await countLotsNeedingPhotoUpload().catch(() => 0);
    say(
      waiting
        ? `Offline, ${lotsWord(waiting)} waiting — they upload on their own when the connection is back.`
        : "Offline — lots will upload when you're back online."
    );
    return null;
  }
  const mediaAllowed = use === "usable" || Boolean(opts.allowCellular);

  uploadPause.clear();
  try {
    say("Checking the website for lots already cataloged…");
    // One website index per Sync, shared by every phase below.
    const websiteIndex = await loadWebsiteIndex(token);
    const videosAttached = await attachPendingLotVideos(token, websiteIndex, mediaAllowed);
    if (videosAttached) {
      opts.onProgress?.();
      say(`Uploaded ${videosAttached} leftover video${videosAttached === 1 ? "" : "s"} to the website.`);
    }
    const archivedFirst = await sweepArchiveConfirmedDrafts(token, websiteIndex);
    if (archivedFirst) {
      opts.onProgress?.();
      say(
        `Moved ${archivedFirst} cataloged lot${archivedFirst === 1 ? "" : "s"} to Phone archive — photos were removed from this phone. Website listings stay.`
      );
    }

    const needingUpload = await countLotsNeedingPhotoUpload();
    const needingCheck = await countLotsNeedingWebsiteCheck();
    if (needingCheck === 0) {
      if (!archivedFirst && !videosAttached) {
        say("Nothing queued to upload. Cataloged lots stay in Phone archive.");
      }
      return { uploaded: videosAttached, failed: 0, reconciled: 0, archived: archivedFirst };
    }

    if (needingUpload > 0 && mediaAllowed) {
      say(`Uploading ${lotsWord(needingUpload)} not on the website yet…`);
    } else {
      say("Checking leftover lots on the website…");
    }
    const result: SyncResult = await syncPendingLots(token, opts.onProgress, websiteIndex, {
      manual: Boolean(opts.manual),
      mediaAllowed
    });
    result.archived = (result.archived || 0) + archivedFirst;
    say(formatSyncOutcome(result));
    return result;
  } finally {
    uploadPause.clear();
  }
}
