using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

using ATAS.Indicators;

namespace TradingHelper.AtasBridge;

/// <summary>
/// Streams what ATAS sees on this chart to the local Trading Cockpit
/// (POST http://127.0.0.1:8780/api/atas/ingest): closed + live candles with the
/// footprint per price level, delta / max-min delta, OI, POC / value area, big
/// cumulative trades and a DOM summary. Read-only: it never touches orders.
/// The indicator only collects; all pattern logic lives in agent/atas_feed.py.
/// </summary>
[DisplayName("Trading Helper Bridge")]
[Category("Other")]
[Display(Description = "Envia footprint, delta, OI, big trades y DOM al Trading Cockpit local. No opera.")]
public class TradingHelperBridge : Indicator
{
    private const int MaxQueuedBars = 5000;
    private const int BarsPerRequest = 100;

    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(10) };
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    private readonly object _lock = new();
    private readonly LinkedList<Dictionary<string, object?>> _bars = new();
    private readonly Dictionary<string, Dictionary<string, object?>> _trades = new();
    private Dictionary<string, object?>? _dom;
    private int _lastClosedSent = -1;
    private DateTime _lastLiveSent = DateTime.MinValue;
    private CancellationTokenSource? _cts;

    [Display(Name = "Endpoint del cockpit", GroupName = "Cockpit", Order = 10)]
    public string Endpoint { get; set; } = "http://127.0.0.1:8780/api/atas/ingest";

    [Display(Name = "Barras de historia a enviar", GroupName = "Cockpit", Order = 20)]
    public int HistoryBars { get; set; } = 500;

    [Display(Name = "Vela viva: cada N segundos", GroupName = "Cockpit", Order = 30)]
    public int LiveIntervalSec { get; set; } = 2;

    [Display(Name = "Footprint por nivel", GroupName = "Cockpit", Order = 40)]
    public bool SendLevels { get; set; } = true;

    [Display(Name = "Big trade: volumen minimo (contratos)", GroupName = "Big trades", Order = 50)]
    public decimal BigTradeMinVolume { get; set; } = 250m;

    [Display(Name = "DOM: rango % alrededor del precio", GroupName = "DOM", Order = 60)]
    public decimal DomRangePct { get; set; } = 1.0m;

    public TradingHelperBridge()
        : base(true)
    {
        DenyToChangePanel = true;
        DataSeries[0].IsHidden = true;
    }

    protected override void OnCalculate(int bar, decimal value)
    {
        EnsureSender();

        if (bar == 0)
        {
            // Full recalculation (chart reload / settings change): resend the history window.
            lock (_lock)
                _bars.Clear();
            _lastClosedSent = -1;
        }

        var liveBar = CurrentBar - 1;
        if (bar < liveBar - Math.Max(1, HistoryBars))
            return;

        if (bar < liveBar)
        {
            if (bar > _lastClosedSent)
            {
                EnqueueBar(BuildBar(bar, closed: true));
                _lastClosedSent = bar;
            }
            return;
        }

        // A new live bar means the previous one just closed: send its final footprint.
        if (bar - 1 >= 0 && bar - 1 > _lastClosedSent)
        {
            EnqueueBar(BuildBar(bar - 1, closed: true));
            _lastClosedSent = bar - 1;
        }

        var now = DateTime.UtcNow;
        if ((now - _lastLiveSent).TotalSeconds < Math.Max(1, LiveIntervalSec))
            return;
        _lastLiveSent = now;
        EnqueueBar(BuildBar(bar, closed: false));
        CaptureDom();
    }

    protected override void OnCumulativeTrade(CumulativeTrade trade) => AddTrade(trade);

    protected override void OnUpdateCumulativeTrade(CumulativeTrade trade) => AddTrade(trade);

    protected override void OnDispose()
    {
        _cts?.Cancel();
        _cts = null;
    }

    private Dictionary<string, object?> BuildBar(int bar, bool closed)
    {
        var c = GetCandle(bar);
        var row = new Dictionary<string, object?>
        {
            ["time"] = Iso(c.Time),
            ["last_time"] = Iso(c.LastTime),
            ["closed"] = closed,
            ["open"] = c.Open,
            ["high"] = c.High,
            ["low"] = c.Low,
            ["close"] = c.Close,
            ["volume"] = c.Volume,
            ["ask"] = c.Ask,
            ["bid"] = c.Bid,
            ["delta"] = c.Delta,
            ["max_delta"] = c.MaxDelta,
            ["min_delta"] = c.MinDelta,
            ["ticks"] = c.Ticks,
            ["oi"] = c.OI,
            ["max_oi"] = c.MaxOI,
            ["min_oi"] = c.MinOI,
            ["vwap"] = c.VWAP,
            ["poc"] = c.MaxVolumePriceInfo?.Price,
            ["vah"] = c.ValueArea?.ValueAreaHigh,
            ["val"] = c.ValueArea?.ValueAreaLow,
        };

        if (SendLevels)
        {
            // [price, bid, ask] per level; Python derives imbalances / absorption from it.
            row["levels"] = c.GetAllPriceLevels()
                .Where(l => l != null)
                .OrderBy(l => l.Price)
                .Select(l => new[] { l.Price, l.Bid, l.Ask })
                .ToArray();
        }

        return row;
    }

    private void AddTrade(CumulativeTrade trade)
    {
        if (trade == null || BigTradeMinVolume <= 0 || trade.Volume < BigTradeMinVolume)
            return;

        // Updates of the same aggregated trade overwrite the previous version.
        var key = $"{trade.Time.Ticks}:{trade.FirstPrice}:{trade.Direction}";
        var row = new Dictionary<string, object?>
        {
            ["time"] = Iso(trade.Time),
            ["first_price"] = trade.FirstPrice,
            ["last_price"] = trade.Lastprice,
            ["volume"] = trade.Volume,
            ["direction"] = trade.Direction.ToString(),
        };

        lock (_lock)
            _trades[key] = row;
    }

    private void CaptureDom()
    {
        try
        {
            var snapshot = MarketDepthInfo?.GetMarketDepthSnapshot()?.ToList();
            if (snapshot == null)
                return;

            var bids = snapshot.Where(x => x.IsBid && x.Volume > 0).ToList();
            var asks = snapshot.Where(x => x.IsAsk && x.Volume > 0).ToList();
            if (bids.Count == 0 || asks.Count == 0)
                return;

            var bestBid = bids.Max(x => x.Price);
            var bestAsk = asks.Min(x => x.Price);
            var mid = (bestBid + bestAsk) / 2m;
            var range = mid * DomRangePct / 100m;
            var nearBids = bids.Where(x => mid - x.Price <= range).ToList();
            var nearAsks = asks.Where(x => x.Price - mid <= range).ToList();

            var dom = new Dictionary<string, object?>
            {
                ["time"] = Iso(DateTime.UtcNow),
                ["best_bid"] = bestBid,
                ["best_ask"] = bestAsk,
                ["range_pct"] = DomRangePct,
                ["bid_volume"] = nearBids.Sum(x => x.Volume),
                ["ask_volume"] = nearAsks.Sum(x => x.Volume),
                ["cum_bids"] = MarketDepthInfo?.CumulativeDomBids,
                ["cum_asks"] = MarketDepthInfo?.CumulativeDomAsks,
                ["bid_walls"] = Walls(nearBids),
                ["ask_walls"] = Walls(nearAsks),
            };

            lock (_lock)
                _dom = dom;
        }
        catch
        {
            // DOM is optional (some feeds / replay have none).
        }
    }

    private static decimal[][] Walls(IEnumerable<MarketDataArg> side) =>
        side.OrderByDescending(x => x.Volume).Take(3).Select(x => new[] { x.Price, x.Volume }).ToArray();

    private void EnqueueBar(Dictionary<string, object?> row)
    {
        lock (_lock)
        {
            // The live bar is resent every few seconds: keep only its latest version.
            if (!(bool)row["closed"]! && _bars.Last is { } last && !(bool)last.Value["closed"]! && Equals(last.Value["time"], row["time"]))
                _bars.RemoveLast();

            _bars.AddLast(row);
            while (_bars.Count > MaxQueuedBars)
                _bars.RemoveFirst();
        }
    }

    private void EnsureSender()
    {
        if (_cts != null)
            return;

        _cts = new CancellationTokenSource();
        var token = _cts.Token;
        _ = Task.Run(() => SendLoop(token));
    }

    private async Task SendLoop(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                await Task.Delay(1000, token);
            }
            catch (OperationCanceledException)
            {
                return;
            }

            List<Dictionary<string, object?>> bars;
            List<Dictionary<string, object?>> trades;
            Dictionary<string, object?>? dom;
            lock (_lock)
            {
                bars = _bars.Take(BarsPerRequest).ToList();
                for (var i = 0; i < bars.Count; i++)
                    _bars.RemoveFirst();
                trades = _trades.Values.ToList();
                _trades.Clear();
                dom = _dom;
                _dom = null;
            }

            if (bars.Count == 0 && trades.Count == 0 && dom == null)
                continue;

            var payload = new Dictionary<string, object?>
            {
                ["source"] = "atas",
                ["version"] = 1,
                ["instrument"] = InstrumentInfo?.Instrument,
                ["exchange"] = InstrumentInfo?.Exchange,
                ["timeframe"] = ChartInfo?.TimeFrame,
                ["chart_type"] = ChartInfo?.ChartType,
                ["tick_size"] = InstrumentInfo?.TickSize,
                ["sent_utc"] = Iso(DateTime.UtcNow),
                ["market_time"] = Iso(MarketTime),
                ["bars"] = bars,
                ["trades"] = trades,
                ["dom"] = dom,
            };

            var ok = false;
            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Post, Endpoint)
                {
                    Content = new StringContent(JsonSerializer.Serialize(payload, JsonOptions), Encoding.UTF8, "application/json"),
                };
                request.Headers.Add("X-Atas-Bridge", "1");
                using var response = await Http.SendAsync(request, token);
                ok = response.IsSuccessStatusCode;
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested)
            {
                return;
            }
            catch
            {
                // Cockpit not running: keep closed bars for later, drop the rest.
            }

            if (!ok)
            {
                lock (_lock)
                {
                    foreach (var row in Enumerable.Reverse(bars).Where(r => (bool)r["closed"]!))
                        _bars.AddFirst(row);
                    while (_bars.Count > MaxQueuedBars)
                        _bars.RemoveFirst();
                }

                try
                {
                    await Task.Delay(4000, token);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
            }
        }
    }

    private static string Iso(DateTime time) => time.ToString("yyyy-MM-dd'T'HH:mm:ss.fff");
}
