import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { fetchAuthSession } from 'aws-amplify/auth';
import { API_URL } from '../aws-config';

const REPORT_TYPES = [
  { id: 'daily_summary', label: 'Daily Summary', description: 'Total calls, answer types, agent volume' },
  { id: 'agent_performance', label: 'Agent Performance', description: 'Scorecard averages, resolution rates by agent' },
  { id: 'outbound_callbacks', label: 'Outbound Callbacks', description: 'Human answer rate, voicemails, duration breakdown' },
];

async function pollForResult(url: string, token: string, maxAttempts = 60): Promise<Record<string, string>> {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const res = await fetch(url, {
      headers: { 'Authorization': `Bearer ${token}` },
    });
    if (!res.ok) continue;
    const data = await res.json();
    if (data.status !== 'processing') return data;
  }
  throw new Error('Report generation timed out');
}

export default function ReportsPage() {
  const [reportType, setReportType] = useState('daily_summary');
  const [dateFrom, setDateFrom] = useState(new Date().toISOString().split('T')[0]);
  const [dateTo, setDateTo] = useState(new Date().toISOString().split('T')[0]);
  const [report, setReport] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const generateReport = async () => {
    setLoading(true);
    setError('');
    setReport('');

    try {
      const session = await fetchAuthSession();
      const token = session.tokens?.idToken?.toString();

      // Start async job
      const res = await fetch(`${API_URL}/report`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ report_type: reportType, date_from: dateFrom, date_to: dateTo }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to generate report');

      // Poll for result
      const result = await pollForResult(`${API_URL}/report?job_id=${data.job_id}`, token!);

      if (result.status === 'error') {
        throw new Error(result.error || 'Report generation failed');
      }

      setReport(result.report);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-lg font-semibold text-gray-900 mb-1">Generate Report</h1>
      <p className="text-sm text-gray-500 mb-6">Select a report type and date range</p>

      <div className="bg-white rounded-xl border border-gray-200 p-5 mb-6 space-y-4">
        {/* Report Type */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">Report Type</label>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {REPORT_TYPES.map(rt => (
              <button
                key={rt.id}
                onClick={() => setReportType(rt.id)}
                className={`text-left p-3 rounded-lg border text-sm transition-colors ${reportType === rt.id
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-gray-200 hover:border-gray-300 text-gray-700'
                  }`}
              >
                <div className="font-medium">{rt.label}</div>
                <div className="text-xs text-gray-500 mt-0.5">{rt.description}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Date Range */}
        <div className="flex gap-4">
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 mb-1">From</label>
            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
          </div>
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 mb-1">To</label>
            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
          </div>
        </div>

        <button
          onClick={generateReport} disabled={loading}
          className="w-full bg-blue-600 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {loading ? 'Generating...' : 'Generate Report'}
        </button>
      </div>

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm mb-4">{error}</div>
      )}

      {report && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex justify-between items-center mb-4">
            <h2 className="font-medium text-gray-900">Report Results</h2>
            <button
              onClick={() => navigator.clipboard.writeText(report)}
              className="text-sm text-blue-600 hover:text-blue-700"
            >
              Copy
            </button>
          </div>
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown>{report}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
