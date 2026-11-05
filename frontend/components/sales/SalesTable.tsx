"use client";

import { useMemo } from "react";
import type { ColumnDef, SortingState } from "@tanstack/react-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";

export type SaleManageRow = {
  id: string;
  name: string;
  closed_at: string | null;
  created_at: string;
  ctseller_sale_id?: number | null;
  lot_count?: number;
  job_count?: number;
  active_job_count?: number;
};

export type SalePending =
  | { kind: "delete"; id: string }
  | { kind: "move"; id: string; targetId: string }
  | { kind: "close"; id: string }
  | { kind: "reopen"; id: string }
  | { kind: "rename"; id: string };

const INITIAL_SORT: SortingState = [
  { id: "status", desc: false },
  { id: "name", desc: false },
];

function saleLabel(sale: SaleManageRow | undefined, fallback: string): string {
  return sale?.name?.trim() || fallback;
}

function confirmLabel(kind: SalePending["kind"]): string {
  if (kind === "delete") return "Delete catalog";
  if (kind === "move") return "Move lots";
  if (kind === "close") return "Close sale";
  if (kind === "reopen") return "Reopen sale";
  return "Save name";
}

function confirmCopy(pending: SalePending, sale: SaleManageRow, sales: SaleManageRow[]): string {
  const lots = sale.lot_count || 0;
  if (pending.kind === "delete") {
    const linked = sale.ctseller_sale_id
      ? " This catalog is linked to a CT Bids auction. If that auction is still open, Sync can add the name back — Close it instead if you only want it off the working list."
      : "";
    return `Delete “${sale.name}” and its ${lots} lot${lots === 1 ? "" : "s"} from Estate-EZ? Photos on this website go with it. CT Bids listings stay up.${linked}`;
  }
  if (pending.kind === "move") {
    const target = sales.find((row) => row.id === pending.targetId);
    return `Move ${lots} lot${lots === 1 ? "" : "s"} from “${sale.name}” into “${saleLabel(target, "the other sale")}”, then remove “${sale.name}” from this list? CT Bids auctions stay separate.`;
  }
  if (pending.kind === "close") {
    return `Close “${sale.name}”? It stays in this list and drops off the open catalog picker.`;
  }
  if (pending.kind === "reopen") {
    return `Reopen “${sale.name}” so it can take new photos again?`;
  }
  return "";
}

type SalesTableProps = {
  sales: SaleManageRow[];
  allSales: SaleManageRow[];
  openSales: SaleManageRow[];
  owner: boolean;
  pending: SalePending | null;
  busyId: string;
  moveTargetById: Record<string, string>;
  renameById: Record<string, string>;
  onOpen: (saleId: string) => void;
  onPending: (pending: SalePending | null) => void;
  onRenameDraft: (saleId: string, name: string) => void;
  onMoveTarget: (saleId: string, targetId: string) => void;
  onConfirm: () => void;
};

export function SalesTable({
  sales,
  allSales,
  openSales,
  owner,
  pending,
  busyId,
  moveTargetById,
  renameById,
  onOpen,
  onPending,
  onRenameDraft,
  onMoveTarget,
  onConfirm,
}: SalesTableProps) {
  const columns = useMemo<ColumnDef<SaleManageRow, unknown>[]>(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Sale",
        cell: ({ row }) => {
          const sale = row.original;
          const active = sale.active_job_count || 0;
          return (
            <div className="min-w-[12rem]">
              <div className="font-semibold text-foreground">{sale.name}</div>
              {active > 0 && owner ? (
                <p className="m-0 mt-1 text-xs text-muted-foreground">
                  Wait until uploads finish before moving or deleting this sale.
                </p>
              ) : null}
            </div>
          );
        },
      },
      {
        id: "status",
        header: "Status",
        accessorFn: (row) => (row.closed_at ? 1 : 0),
        cell: ({ row }) => (
          <Badge variant={row.original.closed_at ? "neutral" : "positive"}>
            {row.original.closed_at ? "Closed" : "Open"}
          </Badge>
        ),
      },
      {
        id: "lots",
        header: "Lots",
        accessorFn: (row) => row.lot_count || 0,
        meta: { numeric: true },
        cell: ({ getValue }) => getValue<number>(),
      },
      {
        id: "jobs",
        header: "Jobs",
        accessorFn: (row) => row.job_count || 0,
        meta: { numeric: true },
        cell: ({ row }) => {
          const jobs = row.original.job_count || 0;
          const active = row.original.active_job_count || 0;
          return (
            <div className="flex flex-col items-end gap-1">
              <span>{jobs}</span>
              {active > 0 ? <Badge variant="warning">{active} uploading</Badge> : null}
            </div>
          );
        },
      },
      {
        id: "ctbids",
        header: "CT Bids",
        accessorFn: (row) => (row.ctseller_sale_id ? 1 : 0),
        cell: ({ row }) =>
          row.original.ctseller_sale_id ? (
            <Badge variant="info">Linked</Badge>
          ) : (
            <span className="text-muted-foreground">—</span>
          ),
      },
      {
        id: "actions",
        header: "Actions",
        enableSorting: false,
        cell: ({ row }) => {
          const sale = row.original;
          const closed = Boolean(sale.closed_at);
          const active = sale.active_job_count || 0;
          const busy = busyId === sale.id;
          const targets = openSales.filter((item) => item.id !== sale.id);
          const targetId = moveTargetById[sale.id] || "";
          return (
            <div className="flex min-w-[16rem] flex-wrap items-center justify-end gap-1.5">
              <Button type="button" size="sm" onClick={() => onOpen(sale.id)}>
                Open
              </Button>
              {owner ? (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => {
                      onRenameDraft(sale.id, sale.name);
                      onPending({ kind: "rename", id: sale.id });
                    }}
                  >
                    Rename
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => onPending({ kind: closed ? "reopen" : "close", id: sale.id })}
                  >
                    {closed ? "Reopen" : "Close"}
                  </Button>
                  {!closed && targets.length > 0 ? (
                    <>
                      <select
                        className="ct-input h-8 max-w-[11rem] py-0 text-sm"
                        aria-label={`Move ${sale.name} into`}
                        value={targetId}
                        disabled={busy || active > 0}
                        onChange={(event) => onMoveTarget(sale.id, event.target.value)}
                      >
                        <option value="">Move into…</option>
                        {targets.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={busy || !targetId || active > 0}
                        onClick={() => onPending({ kind: "move", id: sale.id, targetId })}
                      >
                        Move
                      </Button>
                    </>
                  ) : null}
                  <Button
                    type="button"
                    size="sm"
                    variant="danger-outline"
                    disabled={busy || active > 0}
                    onClick={() => onPending({ kind: "delete", id: sale.id })}
                  >
                    Delete
                  </Button>
                </>
              ) : null}
            </div>
          );
        },
      },
    ],
    [busyId, moveTargetById, onMoveTarget, onOpen, onPending, onRenameDraft, openSales, owner],
  );

  return (
    <DataTable
      columns={columns}
      data={sales}
      getRowId={(sale) => sale.id}
      initialSorting={INITIAL_SORT}
      noun="sales"
      empty="No sales match this list."
      renderSubRow={(sale) => {
        const rowPending = pending?.id === sale.id ? pending : null;
        if (!rowPending) return null;
        const busy = busyId === sale.id;
        return (
          <div className="ct-banner ct-banner--warning" style={{ margin: 0 }}>
            {rowPending.kind === "rename" ? (
              <label className="ct-label" style={{ display: "block", marginBottom: "0.55rem" }}>
                Sale name
                <input
                  className="ct-input"
                  style={{ display: "block", marginTop: 6, maxWidth: 360 }}
                  value={renameById[sale.id] ?? sale.name}
                  onChange={(event) => onRenameDraft(sale.id, event.target.value)}
                />
              </label>
            ) : (
              <p style={{ margin: "0 0 0.55rem" }}>{confirmCopy(rowPending, sale, allSales)}</p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={rowPending.kind === "delete" ? "danger" : "default"}
                disabled={busy}
                onClick={onConfirm}
              >
                {busy ? "Saving…" : confirmLabel(rowPending.kind)}
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => onPending(null)}>
                Cancel
              </Button>
            </div>
          </div>
        );
      }}
    />
  );
}
