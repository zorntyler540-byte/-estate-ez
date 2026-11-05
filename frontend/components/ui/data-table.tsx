"use client";

import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type SortingState,
} from "@tanstack/react-table";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

type DataTableProps<T> = {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  getRowId: (row: T) => string;
  initialSorting?: SortingState;
  pageSize?: number;
  empty?: ReactNode;
  noun?: string;
  renderSubRow?: (row: T) => ReactNode | null;
};

/**
 * Estate-EZ table pattern adapted from ephraimduncan/table-05 (21st.dev):
 * sortable headers, row cells (badges and actions live in column defs), and pagination.
 * Search stays with the page so existing filters are unchanged.
 */
export function DataTable<T>({
  columns,
  data,
  getRowId,
  initialSorting = [],
  pageSize = 25,
  empty = "Nothing to show.",
  noun = "rows",
  renderSubRow,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize });

  useEffect(() => {
    const maxPage = Math.max(0, Math.ceil(data.length / pagination.pageSize) - 1);
    if (pagination.pageIndex > maxPage) {
      setPagination((prev) => ({ ...prev, pageIndex: maxPage }));
    }
  }, [data.length, pagination.pageIndex, pagination.pageSize]);

  const table = useReactTable({
    data,
    columns,
    state: { sorting, pagination },
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    getRowId: (row) => getRowId(row),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  const pageRows = table.getRowModel().rows;
  const total = data.length;
  const first = total === 0 ? 0 : pagination.pageIndex * pagination.pageSize + 1;
  const last = Math.min(total, (pagination.pageIndex + 1) * pagination.pageSize);

  return (
    <div className="flex flex-col gap-3">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id} className="hover:bg-transparent">
              {group.headers.map((header) => {
                const numeric = Boolean(header.column.columnDef.meta?.numeric);
                const sorted = header.column.getIsSorted();
                return (
                  <TableHead
                    key={header.id}
                    className={cn(numeric && "text-right")}
                    aria-sort={
                      header.column.getCanSort()
                        ? sorted === "asc"
                          ? "ascending"
                          : sorted === "desc"
                            ? "descending"
                            : "none"
                        : undefined
                    }
                  >
                    {header.isPlaceholder ? null : header.column.getCanSort() ? (
                      <button
                        type="button"
                        className={cn(
                          "inline-flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 font-sans text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground",
                          numeric && "w-full justify-end",
                        )}
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span aria-hidden="true" className="text-[0.65rem] text-primary">
                          {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}
                        </span>
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {pageRows.length === 0 ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={columns.length} className="py-8 text-center text-muted-foreground">
                {empty}
              </TableCell>
            </TableRow>
          ) : (
            pageRows.map((row) => {
              const sub = renderSubRow?.(row.original);
              return (
                <Fragment key={row.id}>
                  <TableRow>
                    {row.getVisibleCells().map((cell) => (
                      <TableCell
                        key={cell.id}
                        className={cn(cell.column.columnDef.meta?.numeric && "text-right tabular-nums")}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                  {sub ? (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={row.getVisibleCells().length} className="bg-background/50">
                        {sub}
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
              );
            })
          )}
        </TableBody>
      </Table>
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
        <p className="m-0">
          {total === 0 ? `0 ${noun}` : `${first}–${last} of ${total}`}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex items-center gap-2">
            <span>Rows</span>
            <select
              className="ct-input h-8 py-0"
              aria-label="Rows per page"
              value={pagination.pageSize}
              onChange={(event) => {
                setPagination({ pageIndex: 0, pageSize: Number(event.target.value) });
              }}
            >
              {[10, 25, 50].map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => table.previousPage()}
            disabled={!table.getCanPreviousPage()}
          >
            <ChevronLeft size={14} aria-hidden />
            Previous
          </Button>
          <span>
            Page {total === 0 ? 0 : pagination.pageIndex + 1} of {Math.max(table.getPageCount(), total === 0 ? 0 : 1)}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => table.nextPage()}
            disabled={!table.getCanNextPage()}
          >
            Next
            <ChevronRight size={14} aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}
