import type { NewsItem } from "@/schemas";

const OFFICIAL_FEED_IDS = new Set(["fed", "sec_press"]);

export function isOfficialAnnouncement(item: Pick<NewsItem, "source_id">): boolean {
  return OFFICIAL_FEED_IDS.has(item.source_id ?? "");
}
