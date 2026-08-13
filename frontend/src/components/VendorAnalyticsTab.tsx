import React, { useState, useMemo, useEffect, useCallback, useRef } from "react";
import { getAnalyticsDashboard, getAnalyticsFundsTrend, downloadAnalyticsExport } from "../api/analytics";
import { ApiError } from "../api/client";
import { AnalyticsDashboard, AnalyticsVendor, FundsTrendPoint } from "../types";
import { VENDOR_CATEGORY, CATEGORY_LABEL, CATEGORY_BADGE_CLASS } from "../constants/enums";
import { Chart, registerables } from "chart.js/auto";
import {
  X,
  AlertCircle,
  Filter,
  Search,
  Download
} from "lucide-react";

Chart.register(...registerables);

// Indian Format currency helper
function formatINR(num: number, withSymbol = true): string {
  const formatted = new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: 0
  }).format(num);
  return withSymbol ? `₹${formatted}` : formatted;
}

// Indian Format helper with Lakh / Crore abbreviate
function formatINRAbbr(num: number): string {
  if (num >= 10000000) {
    return `₹${(num / 10000000).toFixed(2)} Cr`;
  }
  if (num >= 100000) {
    return `₹${(num / 100000).toFixed(2)} L`;
  }
  return `₹${new Intl.NumberFormat("en-IN").format(num)}`;
}

// "2026-01-01" -> "Jan 2026" — real months from the backend, dynamic length
// (never a fixed 14), not the old ANALYTICS_MONTHS mock constant.
function formatMonthLabel(iso: string): string {
  const [y, m] = iso.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

const EMPTY_DASHBOARD: AnalyticsDashboard = {
  vendors: [],
  months: [],
  aggregates: [],
  aging_totals: {},
  total_outstanding: 0,
  outstanding_by_category: {}
};

// Fixed display order for the Outstanding-by-category breakdown card —
// mirrors backend/analytics/calculations.py's _OUTSTANDING_CATEGORY_ORDER.
// Labels/colors come from constants/enums.ts (CLAUDE.md rule 7 — shared
// vocabulary, not re-typed here). "Normal" is already P2/P3/P4 vendors
// summed as one bucket (Finance's explicit ask), not three separate lines.
const OUTSTANDING_CATEGORY_KEYS = [
  VENDOR_CATEGORY.MUST_PAY,
  VENDOR_CATEGORY.COMMITMENT,
  VENDOR_CATEGORY.NORMAL,
  VENDOR_CATEGORY.INACTIVE
];
const AGING_BUCKET_LABELS = ["0-30", "31-60", "61-90", "91-120", "120+"];
const AGING_BUCKET_STYLES: Record<string, { bg: string; border: string; text: string; labelText: string }> = {
  "0-30": { bg: "bg-[#e8f5e9]", border: "border-emerald-200", text: "text-[#0e7a45]", labelText: "text-[#0e7a45]" },
  "31-60": { bg: "bg-[#c8e6c9]", border: "border-emerald-300", text: "text-emerald-900", labelText: "text-emerald-800" },
  "61-90": { bg: "bg-[#81c784]", border: "border-emerald-400", text: "text-emerald-950", labelText: "text-emerald-900" },
  "91-120": { bg: "bg-[#388e3c]", border: "border-emerald-700", text: "text-white", labelText: "text-emerald-950" },
  "120+": { bg: "bg-[#1b5e20]", border: "border-emerald-950", text: "text-white", labelText: "text-[#1b5e20]" },
};

interface VendorAnalyticsTabProps {
  // Bump this from the parent after a successful upload to force a reload —
  // same refreshSignal convention as MasterDataGrid.tsx/PlanningView.tsx.
  // Without this, this tab (mounted once at app start, kept alive but hidden
  // via CSS when switching tabs — see App.tsx) never re-fetches, so a fresh
  // upload only shows up after a full page reload.
  refreshSignal?: number;
}

export default function VendorAnalyticsTab({ refreshSignal }: VendorAnalyticsTabProps) {
  const [dashboard, setDashboard] = useState<AnalyticsDashboard>(EMPTY_DASHBOARD);
  const [fundsTrend, setFundsTrend] = useState<FundsTrendPoint[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [isExporting, setIsExporting] = useState(false);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    setLoadError("");
    try {
      const [dash, trend] = await Promise.all([getAnalyticsDashboard(), getAnalyticsFundsTrend()]);
      setDashboard(dash);
      setFundsTrend(trend.trend);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);

  const handleDownloadExport = async () => {
    setIsExporting(true);
    try {
      const { blob, filename } = await downloadAnalyticsExport();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? "Vendor Analytics.xlsx";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setIsExporting(false);
    }
  };

  const {
    vendors,
    months,
    aggregates,
    aging_totals: agingTotals,
    // Defensive defaults — guards a stale cached/older-shape API response
    // (e.g. mid-deploy) from throwing rather than just showing ₹0/blank.
    total_outstanding: totalOutstanding = 0,
    outstanding_by_category: outstandingByCategory = {}
  } = dashboard;

  // Search filter state for vendor table
  const [searchTerm, setSearchTerm] = useState("");

  // Clicking an Aging bucket filters the vendor table below to vendors whose
  // oldest_bucket matches — same field the table's own "Bucket" column
  // already shows, so the filter and the visible badge always agree.
  // Clicking the same bucket again clears it.
  const [bucketFilter, setBucketFilter] = useState<string | null>(null);
  const vendorTableRef = useRef<HTMLDivElement | null>(null);

  const handleBucketClick = (label: string) => {
    setBucketFilter((current) => (current === label ? null : label));
    vendorTableRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };

  // Filtered vendors based on search term + selected aging bucket
  const filteredVendors = useMemo(() => {
    let result = vendors;
    if (bucketFilter) {
      result = result.filter((v) => v.oldest_bucket === bucketFilter);
    }
    if (searchTerm.trim()) {
      const term = searchTerm.toLowerCase();
      result = result.filter(
        (v) =>
          v.vendor_name.toLowerCase().includes(term) ||
          v.erp_code.toLowerCase().includes(term)
      );
    }
    return result;
  }, [vendors, searchTerm, bucketFilter]);

  // State for selected vendor row for detail overlay panel
  const [selectedVendor, setSelectedVendor] = useState<AnalyticsVendor | null>(null);

  // References for Chart.js canvas elements
  const cashFlowChartRef = useRef<HTMLCanvasElement | null>(null);
  const consistencyChartRef = useRef<HTMLCanvasElement | null>(null);
  const trendChartRef = useRef<HTMLCanvasElement | null>(null);

  // References for detail modal charts
  const vendorHistoryChartRef = useRef<HTMLCanvasElement | null>(null);
  const vendorConsistencyChartRef = useRef<HTMLCanvasElement | null>(null);
  const vendorTrendChartRef = useRef<HTMLCanvasElement | null>(null);

  // Active chart instances refs for proper cleanup
  const cashFlowChartInstance = useRef<Chart | null>(null);
  const consistencyChartInstance = useRef<Chart | null>(null);
  const trendChartInstance = useRef<Chart | null>(null);

  const vendorHistoryChartInstance = useRef<Chart | null>(null);
  const vendorConsistencyChartInstance = useRef<Chart | null>(null);
  const vendorTrendChartInstance = useRef<Chart | null>(null);

  // KPI card 2's per-category breakdown, in Finance's fixed display order —
  // total_outstanding/outstanding_by_category come straight from the
  // backend (backend/analytics/calculations.py), no client-side re-summing.
  const outstandingRows = useMemo(
    () => OUTSTANDING_CATEGORY_KEYS.map((key) => ({ key, amount: outstandingByCategory[key] ?? 0 })),
    [outstandingByCategory]
  );

  const totalAgingSum = useMemo(() => {
    return AGING_BUCKET_LABELS.reduce((sum, label) => sum + (agingTotals[label] ?? 0), 0) || 1;
  }, [agingTotals]);

  // Bug fix: bar heights used to be fixed Tailwind classes (h-[45%]/58%/...)
  // regardless of real data, so the Aging visual showed the same shape even
  // with zero vendors uploaded. Heights are now proportional to the tallest
  // real bucket — 0 (no bar) when that bucket is genuinely empty, a small
  // visible floor for any nonzero value so it doesn't disappear next to a
  // much larger bucket.
  const maxAgingBucket = useMemo(
    () => Math.max(...AGING_BUCKET_LABELS.map((label) => agingTotals[label] ?? 0), 0),
    [agingTotals]
  );
  const agingBarHeightPct = (label: string) => {
    const value = agingTotals[label] ?? 0;
    if (maxAgingBucket <= 0 || value <= 0) return 0;
    return Math.max(6, (value / maxAgingBucket) * 95);
  };

  // --- RENDERING MAIN DASHBOARD CHARTS ---
  useEffect(() => {
    // 1. Minimum Funds vs Available Funds — grouped bar, not a line: a line
    // implies a continuous trend between points, but fundsTrend usually has
    // just one or two real PlanRun cycles, so a line chart drew a near-flat
    // 2-point segment with no real "trend" to show. A bar per month reads
    // correctly even with a single cycle.
    if (cashFlowChartRef.current) {
      if (cashFlowChartInstance.current) {
        cashFlowChartInstance.current.destroy();
      }

      const trendMonths = fundsTrend.map((c) => formatMonthLabel(c.month));
      const minRequired = fundsTrend.map((c) => c.min_funds_required);
      const available = fundsTrend.map((c) => c.available_funds);

      const ctx = cashFlowChartRef.current.getContext("2d");
      if (ctx) {
        cashFlowChartInstance.current = new Chart(ctx, {
          type: "bar",
          data: {
            labels: trendMonths,
            datasets: [
              {
                label: "Minimum Funds Required",
                data: minRequired,
                backgroundColor: "#10b981", // Bright Emerald Green
                borderRadius: 4,
                borderSkipped: false,
                maxBarThickness: 48
              },
              {
                label: "Available Funds",
                data: available,
                backgroundColor: "#0e7a45", // Dark Forest Green
                borderRadius: 4,
                borderSkipped: false,
                maxBarThickness: 48
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                position: "bottom",
                labels: { boxWidth: 12, font: { size: 10, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              },
              tooltip: {
                callbacks: {
                  label: (context) => `${context.dataset.label}: ${formatINRAbbr(context.raw as number)}`
                }
              }
            },
            scales: {
              x: {
                grid: { display: false },
                border: { display: false },
                ticks: { font: { size: 9, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              },
              y: {
                grid: { display: false },
                border: { display: false },
                ticks: { display: false }
              }
            }
          }
        });
      }
    }

    // 2. Consistency Chart (Aggregate Payment/Payable Ratio)
    if (consistencyChartRef.current) {
      if (consistencyChartInstance.current) {
        consistencyChartInstance.current.destroy();
      }

      const ratios = aggregates.map((a) => (a.total_payable > 0 ? Math.round((a.total_payment / a.total_payable) * 100) : 0));
      const monthLabels = months.map(formatMonthLabel);
      // Bug fix: a hardcoded max: 110 clipped any month that actually paid
      // over 110% of that month's payable (a real case — overpayment/catch-up
      // months exist in the real ledger) flat against the chart's top edge.
      // Headroom above the real peak instead of a fixed ceiling.
      const yMax = Math.max(110, Math.ceil((Math.max(...ratios, 0) + 10) / 10) * 10);

      const ctx = consistencyChartRef.current.getContext("2d");
      if (ctx) {
        consistencyChartInstance.current = new Chart(ctx, {
          type: "line",
          data: {
            labels: monthLabels,
            datasets: [
              {
                label: "Aggregate Payment/Payable Ratio (%)",
                data: ratios,
                borderColor: "#0e7a45", // Forest Green matching screenshot
                backgroundColor: "rgba(14, 122, 69, 0.08)",
                borderWidth: 2.5,
                pointRadius: 4,
                tension: 0.4,
                fill: true
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                position: "bottom",
                labels: { boxWidth: 12, font: { size: 10, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              }
            },
            scales: {
              x: {
                grid: { display: false },
                border: { display: false },
                ticks: { font: { size: 9, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              },
              y: {
                min: 40,
                max: yMax,
                grid: { display: false },
                border: { display: false },
                ticks: { display: false }
              }
            }
          }
        });
      }
    }

    // 3. Trend Chart (Payment Ratio + smoothed trend path)
    if (trendChartRef.current) {
      if (trendChartInstance.current) {
        trendChartInstance.current.destroy();
      }

      const ratios = aggregates.map((a) => (a.total_payable > 0 ? (a.total_payment / a.total_payable) * 100 : 0));
      const monthLabels = months.map(formatMonthLabel);
      // Same headroom fix as the consistency chart above — a fixed max: 110
      // clips any month that paid over 110% of that month's payable.
      const trendYMax = Math.max(110, Math.ceil((Math.max(...ratios, 0) + 10) / 10) * 10);

      // Simple Linear Regression: y = mx + c
      const n = ratios.length;
      let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
      for (let i = 0; i < n; i++) {
        sumX += i;
        sumY += ratios[i];
        sumXY += i * ratios[i];
        sumXX += i * i;
      }
      const slope = n > 1 ? (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX) : 0;

      const trendlineData = ratios.map((val, idx) => {
        const start = Math.max(0, idx - 1);
        const end = Math.min(ratios.length - 1, idx + 1);
        let sum = 0;
        for (let i = start; i <= end; i++) {
          sum += ratios[i];
        }
        return Math.round(sum / (end - start + 1));
      });

      const ctx = trendChartRef.current.getContext("2d");
      if (ctx) {
        trendChartInstance.current = new Chart(ctx, {
          type: "line",
          data: {
            labels: monthLabels,
            datasets: [
              {
                label: "Actual Payment Ratio (%)",
                data: ratios.map(r => Math.round(r)),
                borderColor: "#64748b",
                backgroundColor: "transparent",
                borderWidth: 2,
                pointRadius: 3,
                tension: 0.4,
                fill: false
              },
              {
                label: "Linear Trend Path",
                data: trendlineData,
                borderColor: slope >= 0 ? "#10b981" : "#64748b", // Green if improving, slate-gray if worsening
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.4,
                fill: false
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                position: "bottom",
                labels: { boxWidth: 12, font: { size: 10, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              }
            },
            scales: {
              x: {
                grid: { display: false },
                border: { display: false },
                ticks: { font: { size: 9, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              },
              y: {
                min: 40,
                max: trendYMax,
                grid: { display: false },
                border: { display: false },
                ticks: { display: false }
              }
            }
          }
         });
       }
     }

     // Clean up instances on unmount
    return () => {
      if (cashFlowChartInstance.current) cashFlowChartInstance.current.destroy();
      if (consistencyChartInstance.current) consistencyChartInstance.current.destroy();
      if (trendChartInstance.current) trendChartInstance.current.destroy();
    };
  }, [aggregates, months, fundsTrend]);


  // --- RENDERING SELECTED VENDOR DETAIL CHARTS ---
  useEffect(() => {
    if (!selectedVendor) return;

    const monthLabels = selectedVendor.history.map((h) => formatMonthLabel(h.month));

    // 1. Full-width Vertical Bar Graph: payments vs consumption (payable) with values on top
    if (vendorHistoryChartRef.current) {
      if (vendorHistoryChartInstance.current) {
        vendorHistoryChartInstance.current.destroy();
      }

      const payables = selectedVendor.history.map(h => h.payable);
      const payments = selectedVendor.history.map(h => h.payment);

      const ctx = vendorHistoryChartRef.current.getContext("2d");
      if (ctx) {
        vendorHistoryChartInstance.current = new Chart(ctx, {
          type: "bar",
          data: {
            labels: monthLabels,
            datasets: [
              {
                label: "Monthly Consumption (Payable)",
                data: payables,
                backgroundColor: "rgba(16, 185, 129, 0.4)", // Light Emerald / Sage for consumption
                borderColor: "#059669",
                borderWidth: 1,
                borderRadius: 2,
                barPercentage: 0.8,
                categoryPercentage: 0.8,
              },
              {
                label: "Actual Payment Disbursed",
                data: payments,
                backgroundColor: "#0e7a45", // Deep Forest Green for payment
                borderColor: "#064e3b",
                borderWidth: 1,
                borderRadius: 2,
                barPercentage: 0.8,
                categoryPercentage: 0.8,
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                position: "top",
                labels: {
                  font: { size: 10, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" }
                }
              }
            },
            scales: {
              x: {
                grid: { display: false },
                border: { display: false },
                ticks: { font: { size: 9, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              },
              y: {
                grid: { display: false },
                border: { display: false },
                ticks: { display: false },
                grace: "20%" // 20% room on top for the numbers drawn by our custom plugin
              }
            }
          },
          plugins: [
            {
              id: "vendorDatalabels",
              afterDatasetsDraw(chart) {
                const { ctx } = chart;
                ctx.save();
                chart.data.datasets.forEach((dataset, datasetIndex) => {
                  const meta = chart.getDatasetMeta(datasetIndex);
                  meta.data.forEach((bar: any, index) => {
                    const value = dataset.data[index] as number;
                    if (value === 0 || value === null || value === undefined) return;

                    // Concise Indian readable values format (e.g. 1.2L or 15k)
                    let label = "";
                    if (value >= 10000000) {
                      label = `₹${(value / 10000000).toFixed(1)}Cr`;
                    } else if (value >= 100000) {
                      label = `₹${(value / 100000).toFixed(1)}L`;
                    } else if (value >= 1000) {
                      label = `₹${(value / 1000).toFixed(0)}k`;
                    } else {
                      label = `₹${value}`;
                    }

                    ctx.fillStyle = datasetIndex === 0 ? "#059669" : "#0e7a45"; // Sage Green text for consumption, Forest Green for payment
                    ctx.textBaseline = "bottom";

                    // Two bars share one narrow month slot — a label's text
                    // width is often wider than the bar itself, so even a
                    // few px of horizontal offset isn't enough once both
                    // bars are close in height. Stack the two labels on
                    // separate vertical lines (consumption higher, payment
                    // right above its own bar) so they never share a row to
                    // collide on, then nudge horizontally too for the cases
                    // where one bar is much taller than the other. On top
                    // of that, measure the actual rendered width — a big
                    // Cr-range amount ("₹1.2Cr") is only 1-2 chars longer
                    // than a lakh-range one, but with 15+ narrow bars even
                    // that's enough to spill into the next month's label,
                    // so shrink the font just for this label until it fits
                    // within its own bar's slot rather than a fixed size
                    // that only happened to work for the values seen so far.
                    let fontSize = 7;
                    ctx.font = `bold ${fontSize}px monospace`;
                    const slotWidth = Math.max(bar.width ?? 16, 16) * 1.6;
                    while (ctx.measureText(label).width > slotWidth && fontSize > 5) {
                      fontSize -= 0.5;
                      ctx.font = `bold ${fontSize}px monospace`;
                    }

                    ctx.textAlign = datasetIndex === 0 ? "right" : "left";
                    const x = bar.x + (datasetIndex === 0 ? -2 : 2);
                    const y = datasetIndex === 0 ? bar.y - 10 : bar.y - 3;
                    ctx.fillText(label, x, y);
                  });
                });
                ctx.restore();
              }
            }
          ]
        });
      }
    }

    // 2. Vendor Consistency Chart
    if (vendorConsistencyChartRef.current) {
      if (vendorConsistencyChartInstance.current) {
        vendorConsistencyChartInstance.current.destroy();
      }

      const ratios = selectedVendor.history.map(h => h.payable > 0 ? Math.round((h.payment / h.payable) * 100) : 0);
      // Same headroom fix as the dashboard-level consistency chart above —
      // a fixed max: 120 clipped any month that paid over 120% of that
      // month's payable (a real case — overpayment/catch-up months exist).
      const vendorYMax = Math.max(120, Math.ceil((Math.max(...ratios, 0) + 10) / 10) * 10);

      const ctx = vendorConsistencyChartRef.current.getContext("2d");
      if (ctx) {
        vendorConsistencyChartInstance.current = new Chart(ctx, {
          type: "line",
          data: {
            labels: monthLabels,
            datasets: [
              {
                label: "Payment Ratio (%)",
                data: ratios,
                borderColor: "#0e7a45", // Forest Green — matches the app's theme (was an inconsistent blue)
                backgroundColor: "rgba(14, 122, 69, 0.08)",
                borderWidth: 2,
                pointRadius: 3,
                tension: 0.4,
                fill: true
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              x: {
                grid: { display: false },
                border: { display: false },
                ticks: { font: { size: 9, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              },
              y: {
                min: 0,
                max: vendorYMax,
                grid: { display: false },
                border: { display: false },
                ticks: { display: false }
              }
            }
          }
        });
      }
    }

    // 3. Vendor Trend Chart
    if (vendorTrendChartRef.current) {
      if (vendorTrendChartInstance.current) {
        vendorTrendChartInstance.current.destroy();
      }

      const ratios = selectedVendor.history.map(h => h.payable > 0 ? (h.payment / h.payable) * 100 : 0);
      const n = ratios.length;
      let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
      for (let i = 0; i < n; i++) {
        sumX += i;
        sumY += ratios[i];
        sumXY += i * ratios[i];
        sumXX += i * i;
      }
      const slope = n > 1 ? (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX) : 0;
      const intercept = n > 0 ? (sumY - slope * sumX) / n : 0;
      const trendlineData = ratios.map((_, i) => Math.round(slope * i + intercept));

      const ctx = vendorTrendChartRef.current.getContext("2d");
      if (ctx) {
        vendorTrendChartInstance.current = new Chart(ctx, {
          type: "line",
          data: {
            labels: monthLabels,
            datasets: [
              {
                label: "Payment Ratio (%)",
                data: ratios.map(r => Math.round(r)),
                borderColor: "#64748b",
                borderWidth: 1.5,
                pointRadius: 2,
                tension: 0.4,
                fill: false
              },
              {
                label: "Trend Path",
                data: trendlineData.map((val, idx) => {
                  const start = Math.max(0, idx - 1);
                  const end = Math.min(trendlineData.length - 1, idx + 1);
                  let sum = 0;
                  for (let i = start; i <= end; i++) {
                    sum += trendlineData[i];
                  }
                  return Math.round(sum / (end - start + 1));
                }),
                borderColor: slope >= 0 ? "#10b981" : "#64748b",
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.4,
                fill: false
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              x: {
                grid: { display: false },
                border: { display: false },
                ticks: { font: { size: 9, family: '"Plus Jakarta Sans", "Inter", sans-serif', weight: "bold" } }
              },
              y: {
                min: 0,
                max: 120,
                grid: { display: false },
                border: { display: false },
                ticks: { display: false }
              }
            }
          }
        });
      }
    }

    return () => {
      if (vendorHistoryChartInstance.current) vendorHistoryChartInstance.current.destroy();
      if (vendorConsistencyChartInstance.current) vendorConsistencyChartInstance.current.destroy();
      if (vendorTrendChartInstance.current) vendorTrendChartInstance.current.destroy();
    };
  }, [selectedVendor]);

  // Same th/td convention as PlanningView.tsx's own Planning table — this
  // table is meant to look like that one, not carry its own separate style.
  const th = "py-2 px-2.5 whitespace-nowrap";
  const td = "py-2.5 px-2.5 whitespace-nowrap";

  return (
    <div
      className="flex flex-col flex-1 text-[#1e293b] text-sm antialiased pb-12 min-h-screen relative"
      style={{
        backgroundColor: "#f2f7f4",
        backgroundImage: "linear-gradient(to right, rgba(16, 185, 129, 0.08) 1px, transparent 1px), linear-gradient(to bottom, rgba(16, 185, 129, 0.08) 1px, transparent 1px)",
        backgroundSize: "32px 32px"
      }}
    >

      {loadError && (
        <div className="mx-6 mt-4 px-4 py-2.5 rounded-lg bg-red-50 border border-red-200 text-xs font-semibold text-red-700">
          Couldn't load analytics: {loadError}
        </div>
      )}

      {/* MAIN BODY CONTENT */}
      <main className="py-6 flex flex-col gap-6 w-full relative z-10">

        {/* ROW 1: 2 TOP KPI CARDS, 50/50 SPLIT. Overall Debt/Overall Paid to
            Date replaced per Finance's ask (this task) with Total
            Outstanding + a Must Pay/Commitment/Normal/Inactive breakdown. */}
        <section className="grid grid-cols-1 sm:grid-cols-2 gap-4">

          {/* CARD 1: Total Outstanding — sum across every vendor. */}
          <div className="bg-white border border-emerald-100/90 rounded-xl p-5 shadow-2xs hover:shadow-md transition-all relative overflow-hidden flex flex-col justify-between">
            <div className="flex items-start justify-between">
              <div className="w-10 h-10 rounded-full bg-emerald-50 text-[#0e7a45] border border-emerald-200/80 flex items-center justify-center shrink-0">
                <AlertCircle className="w-5 h-5" />
              </div>
            </div>
            <div className="mt-4">
              <span className="text-xs font-medium text-slate-500 block">Total Outstanding</span>
              <span className="text-2xl font-bold text-slate-900 tracking-tight block mt-0.5">
                {formatINR(totalOutstanding)}
              </span>
              <p className="text-[10px] text-slate-400 font-medium mt-1.5">Across all vendors, right now</p>
            </div>
          </div>

          {/* CARD 2: Outstanding by category — Must Pay / Commitment /
              Normal (P2+P3+P4 summed) / Inactive. */}
          <div className="bg-white border border-emerald-100/90 rounded-xl p-5 shadow-2xs hover:shadow-md transition-all relative overflow-hidden flex flex-col">
            <span className="text-xs font-medium text-slate-500 block mb-3">Outstanding by Category</span>
            <div className="flex flex-col gap-2.5">
              {outstandingRows.map(({ key, amount }) => (
                <div key={key} className="flex items-center justify-between gap-3">
                  <span
                    className={`text-[11px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${CATEGORY_BADGE_CLASS[key] ?? "bg-gray-50 text-gray-600"}`}
                  >
                    {CATEGORY_LABEL[key] ?? key}
                  </span>
                  <span className="text-sm font-bold text-slate-900 tracking-tight truncate">
                    {formatINR(amount)}
                  </span>
                </div>
              ))}
            </div>
          </div>

        </section>

        {/* ROW 2: AGING PROFILE, full width (the Min Funds vs Expected
            Funds chart that used to sit beside it was removed per Sarath's
            request — this task) */}
        <section className="grid grid-cols-1 gap-6">

          {/* Aging Profile */}
          <div className="bg-white border border-emerald-100/90 rounded-xl p-5 flex flex-col justify-between shadow-2xs">
            <div>
              <h3 className="text-sm font-bold text-gray-900 mb-4">Aging</h3>
            </div>

            {/* Sequential Block Visual — click a bucket to filter the
                vendor table below to only vendors whose oldest_bucket
                matches (same field the table's own Bucket column shows).
                Bar heights are proportional to real data (agingBarHeightPct
                above), not a fixed shape. */}
            <div className="grid grid-cols-5 gap-2.5 text-center h-32 items-end">

              {AGING_BUCKET_LABELS.map((label) => {
                const styles = AGING_BUCKET_STYLES[label];
                const isActive = bucketFilter === label;
                return (
                  <div key={label} className="flex flex-col gap-1.5 h-full justify-end">
                    <span className={`text-[10px] font-semibold font-mono ${styles.labelText}`}>
                      {formatINRAbbr(agingTotals[label] ?? 0).replace("₹", "")}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleBucketClick(label)}
                      title={`Show vendors in the ${label} bucket`}
                      style={{ height: `${agingBarHeightPct(label)}%` }}
                      className={`${styles.bg} border ${styles.border} rounded-lg flex items-center justify-center transition-all hover:opacity-90 shadow-2xs cursor-pointer w-full ${
                        isActive ? "ring-2 ring-[#0e7a45] ring-offset-1" : ""
                      }`}
                    >
                      <span className={`text-[9px] font-medium font-mono ${styles.text}`}>
                        {(((agingTotals[label] ?? 0) / totalAgingSum) * 100).toFixed(1)}%
                      </span>
                    </button>
                    <span className={`text-[10px] font-medium ${styles.labelText}`}>{label}</span>
                  </div>
                );
              })}

            </div>
          </div>

        </section>

        {/* ROW 3: SINGLE FULL WIDTH CONSISTENCY CHART */}
        <section className="grid grid-cols-1 gap-6">
          <div className="bg-white border border-emerald-100/90 rounded-xl p-5 shadow-2xs flex flex-col justify-between h-75">
            <div>
              <h3 className="text-sm font-bold text-gray-900">Payment Consistency</h3>
            </div>
            <div className="flex-1 relative mt-3 h-52.5">
              <canvas ref={consistencyChartRef} />
            </div>
          </div>
        </section>

        {/* ROW 4: DETAILED VENDOR GRID TABLE (MATCHING SCREENSHOT TABLE BAR & MINT HEADER) */}
        <section ref={vendorTableRef} className="bg-white border border-emerald-100/90 rounded-xl shadow-xs overflow-hidden flex flex-col">

          {/* Table Header Bar with Search & Action Buttons matching Screenshot */}
          <div className="p-4 bg-white border-b border-emerald-100/80 flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4">

            {/* Search Input & Filter */}
            <div className="flex items-center gap-2 flex-1 max-w-md">
              <div className="relative w-full">
                <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Search vendor by name or ERP code..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-lg pl-10 pr-4 py-2 text-xs font-medium text-slate-800 placeholder-slate-400 focus:outline-none focus:border-[#0e7a45] focus:bg-white transition-all shadow-2xs"
                />
              </div>
              <button
                className="p-2 border border-slate-200 bg-slate-50 hover:bg-slate-100 rounded-lg text-slate-600 cursor-pointer transition-colors shadow-2xs shrink-0"
                title="Filter Options"
              >
                <Filter className="w-4 h-4" />
              </button>
              {bucketFilter && (
                <span className="flex items-center gap-1.5 text-[10px] font-semibold text-[#0e7a45] bg-emerald-50 border border-emerald-200 px-2.5 py-1.5 rounded-lg shrink-0 whitespace-nowrap">
                  Bucket: {bucketFilter}
                  <button
                    type="button"
                    onClick={() => setBucketFilter(null)}
                    className="hover:text-emerald-900 cursor-pointer"
                    title="Clear bucket filter"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              )}
            </div>

            {/* Action Buttons in Solid Forest Green */}
            <div className="flex items-center gap-3 shrink-0">
              <button
                onClick={handleDownloadExport}
                disabled={isExporting}
                className="border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-60 text-slate-700 font-bold text-xs px-4 py-2 rounded-lg flex items-center gap-2 transition-all cursor-pointer shadow-2xs"
              >
                <Download className="w-3.5 h-3.5 text-slate-500" />
                <span>{isExporting ? "Downloading…" : "Download Excel"}</span>
              </button>
            </div>
          </div>

          {/* Table styling matches PlanningView.tsx's own Planning table
              exactly (th/td padding, emerald-50 sticky header, single
              sticky column, plain hover rows, border-l-only group
              dividers) rather than its own separate look — same
              table-scroll fixed-height-box convention too. */}
          <div className="table-scroll overscroll-contain max-h-155">
            <table className="w-full text-left text-xs border-separate border-spacing-0">
              <thead className="bg-emerald-50 text-[#107c41] font-semibold sticky top-0 z-30">
                <tr className="border-b border-emerald-100">
                  <th rowSpan={2} className={`${th} sticky left-0 z-40 bg-emerald-50`}>
                    <div className="flex items-center gap-1.5">
                      <span>Vendor</span>
                      <Filter className="w-3 h-3 text-[#107c41]/60 cursor-pointer" />
                    </div>
                  </th>
                  <th rowSpan={2} className={`${th} border-l border-emerald-100 text-right`}>Outstanding</th>

                  {/* Spans for every real month (dynamic length) */}
                  {months.map((m) => (
                    <th key={m} colSpan={2} className={`${th} border-l border-emerald-100 text-center`}>
                      {formatMonthLabel(m)}
                    </th>
                  ))}

                  <th colSpan={AGING_BUCKET_LABELS.length} className={`${th} border-l border-emerald-100 text-center`}>Aging</th>
                </tr>
                <tr className="border-b border-emerald-100">
                  {months.map((m) => (
                    <React.Fragment key={m}>
                      <th className={`${th} text-right`}>Payable</th>
                      <th className={`${th} text-right`}>Paid</th>
                    </React.Fragment>
                  ))}
                  {AGING_BUCKET_LABELS.map((label, i) => (
                    <th key={label} className={`${th} text-right ${i === 0 ? "border-l border-emerald-100" : ""}`}>{label}</th>
                  ))}
                </tr>
              </thead>

              <tbody className="divide-y divide-gray-100 text-gray-700">
                {filteredVendors.map((v) => (
                  <tr key={v.vendor_id} className="hover:bg-gray-50/70 cursor-pointer" onClick={() => setSelectedVendor(v)}>
                    <td className={`${td} sticky left-0 bg-white`}>
                      <div className="flex flex-col">
                        <span className="font-medium text-gray-900">{v.vendor_name}</span>
                        <span className="text-[11px] text-gray-500 font-mono mt-0.5">{v.erp_code}</span>
                      </div>
                    </td>

                    <td className={`${td} border-l border-emerald-100 text-right font-semibold text-gray-900`}>
                      {formatINR(v.outstanding_balance)}
                    </td>

                    {/* Every real month's data */}
                    {v.history.map((h, hIdx) => (
                      <React.Fragment key={hIdx}>
                        <td className={`${td} text-right text-gray-500`}>{formatINR(h.payable, false)}</td>
                        <td className={`${td} text-right text-[#107c41] font-medium`}>{formatINR(h.payment, false)}</td>
                      </React.Fragment>
                    ))}

                    {/* Every aging bucket, side by side — the oldest one
                        (matching v.oldest_bucket) bolded so the concerning
                        figure still stands out without a colored pill. */}
                    {AGING_BUCKET_LABELS.map((label, i) => (
                      <td
                        key={label}
                        className={`${td} text-right ${i === 0 ? "border-l border-emerald-100" : ""} ${
                          label === v.oldest_bucket ? "font-semibold text-gray-900" : "text-gray-500"
                        }`}
                      >
                        {formatINR(v.aging_buckets[label] ?? 0, false)}
                      </td>
                    ))}
                  </tr>
                ))}
                {!isLoading && filteredVendors.length === 0 && (
                  <tr>
                    <td colSpan={2 + months.length * 2 + AGING_BUCKET_LABELS.length} className="px-4 py-10 text-center text-xs text-gray-400">
                      {vendors.length === 0
                        ? "No vendor data yet — upload a master sheet on the Main tab."
                        : bucketFilter
                          ? `No vendors in the ${bucketFilter} bucket.`
                          : "No vendors match this search."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

      </main>

      {/* DETAILED VENDOR OVERLAY PANEL MODAL */}
      {selectedVendor && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4">
          <div className="bg-white border border-emerald-100 rounded-xl shadow-2xl w-full max-w-4xl overflow-hidden flex flex-col relative animate-fade-in max-h-[92vh]">

            {/* Modal Header */}
            <div className="bg-[#0e7a45] text-white px-6 py-4 flex items-center justify-between shrink-0">
              <div>
                <span className="text-[10px] font-mono uppercase bg-white/20 px-2.5 py-0.5 rounded mr-2 font-semibold text-white">
                  {selectedVendor.erp_code}
                </span>
                <span className="text-sm font-bold tracking-tight text-white">{selectedVendor.vendor_name}</span>
              </div>
              <button
                onClick={() => setSelectedVendor(null)}
                className="text-white hover:text-emerald-100 cursor-pointer p-1 rounded-full hover:bg-white/10 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Scrollable Modal Body */}
            <div className="p-6 flex flex-col gap-6 overflow-y-auto max-h-[calc(92vh-100px)]">

              {/* Aging buckets, side by side — same aging_buckets data the
                  main Aging card sums into aging_totals, scoped to just
                  this vendor. */}
              <div>
                <h4 className="text-[10px] font-bold text-[#0e7a45] uppercase tracking-wider mb-2">Aging</h4>
                <div className="grid grid-cols-5 gap-2.5">
                  {AGING_BUCKET_LABELS.map((label) => {
                    const styles = AGING_BUCKET_STYLES[label];
                    const amount = selectedVendor.aging_buckets[label] ?? 0;
                    return (
                      <div
                        key={label}
                        className={`${styles.bg} border ${styles.border} rounded-lg py-2.5 px-1 flex flex-col items-center gap-1`}
                      >
                        <span className={`text-[9px] font-semibold uppercase tracking-wide ${styles.labelText}`}>{label}</span>
                        <span className={`text-[11px] font-bold font-mono ${styles.text}`}>{formatINRAbbr(amount)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Per-month breakdown, oldest -> newest — same monthly_breakdown
                  data aging.py already computes (backend/shared/aging.py). */}
              <div>
                <h4 className="text-[10px] font-bold text-[#0e7a45] uppercase tracking-wider mb-2">Monthly Breakdown</h4>
                <div className="border border-emerald-100 rounded-lg overflow-hidden">
                  <div className="max-h-48 overflow-y-auto">
                    <table className="w-full text-left text-[11px]">
                      <thead className="sticky top-0 bg-emerald-50">
                        <tr className="text-[#0e7a45] font-semibold">
                          <th className="px-3 py-2">Month</th>
                          <th className="px-3 py-2 text-right">Payable</th>
                          <th className="px-3 py-2 text-right">Paid</th>
                          <th className="px-3 py-2 text-right">Aging</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-emerald-100">
                        {selectedVendor.monthly_breakdown.map((row) => (
                          <tr key={row.months_back}>
                            <td className="px-3 py-2 font-medium text-slate-700">{formatMonthLabel(row.month)}</td>
                            <td className="px-3 py-2 text-right text-slate-600">{formatINR(row.payable)}</td>
                            <td className="px-3 py-2 text-right text-[#0e7a45]">{formatINR(row.payment)}</td>
                            <td className="px-3 py-2 text-right font-semibold text-slate-900">{formatINR(row.amount)}</td>
                          </tr>
                        ))}
                        {selectedVendor.monthly_breakdown.length === 0 && (
                          <tr>
                            <td colSpan={4} className="px-3 py-4 text-center text-slate-400">No outstanding balance.</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              {/* Chart A: Full-width line graph of payments vs consumption */}
              <div className="border border-emerald-100 rounded-xl p-5 bg-white flex flex-col justify-between h-[280px]">
                <div>
                  <h4 className="text-xs font-bold text-[#0e7a45] uppercase tracking-wider">Consumption vs Disbursal History ({selectedVendor.history.length} Periods)</h4>
                  <p className="text-[10px] text-slate-500 font-medium">Monthly consumption bills versus actual disbursed capital amounts.</p>
                </div>
                <div className="flex-1 relative mt-3 h-[180px]">
                  <canvas ref={vendorHistoryChartRef} />
                </div>
              </div>

              {/* Row B: Scoped Consistency Chart */}
              <div className="border border-emerald-100 rounded-xl p-5 bg-white flex flex-col justify-between h-[250px]">
                <div>
                  <h4 className="text-xs font-bold text-[#0e7a45] uppercase tracking-wider">Payment Consistency Ratio (%)</h4>
                  <p className="text-[10px] text-slate-500 font-medium">Fulfillment ratio contrasted with a 100% target reference line.</p>
                </div>
                <div className="flex-1 relative mt-3 h-[150px]">
                  <canvas ref={vendorConsistencyChartRef} />
                </div>
              </div>

            </div>

          </div>
        </div>
      )}

    </div>
  );
}
