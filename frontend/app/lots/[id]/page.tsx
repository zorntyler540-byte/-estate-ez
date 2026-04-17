"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch } from "../../../lib/api";

type Lot = {
  id: string;
  sku: string | null;
  auction_title: string;
  professional_description: string;
  specification_lines: string[];
  brands: string[];
  labels_and_tags: string[];
  starting_price: number | null;
  condition_text: string;
  photo_count: number;
};

export default function LotDetailPage() {
  const params = useParams<{ id: string }>();
  const [token, setToken] = useState("");
  const [lot, setLot] = useState<Lot | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t || !params.id) return;
    apiFetch(`/lots/${params.id}`, t)
      .then((d) => setLot(d))
      .catch((e) => setError(e instanceof Error ? e.message : "Load failed"));
  }, [params.id]);

  async function save() {
    if (!lot) return;
    setSaving(true);
    setError("");
    try {
      const updated = await apiFetch(`/lots/${lot.id}`, token, {
        method: "PATCH",
        body: JSON.stringify({
          auction_title: lot.auction_title,
          professional_description: lot.professional_description,
          specification_lines: lot.specification_lines,
          brands: lot.brands,
          labels_and_tags: lot.labels_and_tags,
          starting_price: lot.starting_price,
          condition_text: lot.condition_text
        })
      });
      setLot(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (!token) return <p>Please login first.</p>;
  if (!lot) return <p>Loading lot...</p>;

  return (
    <main>
      <h1>Lot {lot.sku || "(no SKU)"}</h1>
      <label>Auction Title</label>
      <input
        style={{ width: "100%", marginBottom: 8 }}
        value={lot.auction_title}
        onChange={(e) => setLot({ ...lot, auction_title: e.target.value })}
      />
      <label>Professional Description</label>
      <textarea
        style={{ width: "100%", minHeight: 160, marginBottom: 8 }}
        value={lot.professional_description}
        onChange={(e) => setLot({ ...lot, professional_description: e.target.value })}
      />
      <label>Specification Lines (one per line)</label>
      <textarea
        style={{ width: "100%", minHeight: 120, marginBottom: 8 }}
        value={lot.specification_lines.join("\n")}
        onChange={(e) => setLot({ ...lot, specification_lines: e.target.value.split("\n").filter(Boolean) })}
      />
      <label>Brands (comma separated)</label>
      <input
        style={{ width: "100%", marginBottom: 8 }}
        value={lot.brands.join(", ")}
        onChange={(e) => setLot({ ...lot, brands: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
      />
      <label>Sizes / Tags (comma separated)</label>
      <input
        style={{ width: "100%", marginBottom: 8 }}
        value={lot.labels_and_tags.join(", ")}
        onChange={(e) =>
          setLot({ ...lot, labels_and_tags: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })
        }
      />
      <label>Starting Price</label>
      <input
        style={{ width: "100%", marginBottom: 8 }}
        type="number"
        value={lot.starting_price ?? ""}
        onChange={(e) => setLot({ ...lot, starting_price: e.target.value ? Number(e.target.value) : null })}
      />
      <label>Condition</label>
      <input
        style={{ width: "100%", marginBottom: 8 }}
        value={lot.condition_text}
        onChange={(e) => setLot({ ...lot, condition_text: e.target.value })}
      />
      <button onClick={save} disabled={saving}>
        {saving ? "Saving..." : "Save"}
      </button>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
    </main>
  );
}

