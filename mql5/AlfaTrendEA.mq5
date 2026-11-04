//+------------------------------------------------------------------+
//| AlfaTrendEA.mq5                                                  |
//| Автономный трендовый советник для MetaTrader 5 (Альфа-Форекс).   |
//| Та же логика, что у Python-бота alfabot:                         |
//|   вход: пересечение EMA20/EMA50 по тренду EMA200, ADX >= 20,     |
//|         RSI не в зоне перекупленности/перепроданности;           |
//|   выход: SL 1.5 ATR, TP 3 ATR, безубыток +1 ATR, трейлинг 1.5 ATR,|
//|          обратное пересечение;                                   |
//|   риск: % на сделку, дневной стоп, аварийная остановка по        |
//|         просадке, лимит позиций, фильтр спреда, расписание.      |
//| Один экземпляр на одном графике торгует сразу всеми символами    |
//| из параметра InpSymbols.                                         |
//+------------------------------------------------------------------+
#property copyright "alfabot"
#property version   "1.00"

#include <Trade/Trade.mqh>

input string          InpSymbols          = "EURUSD,GBPUSD,USDJPY"; // символы через запятую
input ENUM_TIMEFRAMES InpTimeframe        = PERIOD_H1;
input long            InpMagic            = 770077;
input bool            InpAllowReal        = false;  // false = торговать только на демо
// стратегия
input int    InpEmaFast      = 20;
input int    InpEmaSlow      = 50;
input int    InpEmaTrend     = 200;
input int    InpAdxPeriod    = 14;
input double InpAdxMin       = 20.0;
input int    InpRsiPeriod    = 14;
input double InpRsiOverbought= 70.0;
input double InpRsiOversold  = 30.0;
input int    InpAtrPeriod    = 14;
input double InpSlAtr        = 1.5;
input double InpTpAtr        = 3.0;
input double InpBreakevenAtr = 1.0;
input double InpTrailAtr     = 1.5;
input bool   InpExitOnOpposite = true;
// риск
input double InpRiskPct        = 1.0;   // риск на сделку, % средств
input double InpMaxDailyLossPct= 3.0;   // дневной стоп, %
input double InpMaxDrawdownPct = 15.0;  // аварийная остановка, % от пика
input int    InpMaxPositions   = 3;
input int    InpMaxSpreadPts   = 30;
input double InpMaxLot         = 1.0;
input int    InpDeviationPts   = 20;
// расписание (UTC)
input int    InpStartHourUtc   = 6;
input int    InpEndHourUtc     = 20;
input int    InpFridayNoNewUtc = 18;
input int    InpFridayCloseUtc = 20;    // -1 = не закрывать перед выходными
input bool   InpPush           = true;  // push-уведомления в мобильный MT5

CTrade   trade;
string   g_syms[];
int      g_hFast[], g_hSlow[], g_hTrend[], g_hAdx[], g_hRsi[], g_hAtr[];
datetime g_lastBar[];
bool     g_disabled = false;

string GV(const string name) { return "AlfaTrend_" + IntegerToString(InpMagic) + "_" + name; }

double GVGet(const string name, const double def)
{
   return GlobalVariableCheck(GV(name)) ? GlobalVariableGet(GV(name)) : def;
}

void Notify(const string text)
{
   Print(text);
   if(InpPush && !MQLInfoInteger(MQL_TESTER))
      SendNotification(StringSubstr("AlfaTrend: " + text, 0, 255));
}

//+------------------------------------------------------------------+
int OnInit()
{
   bool demo = AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO;
   if(!demo && !InpAllowReal && !MQLInfoInteger(MQL_TESTER))
   {
      Alert("AlfaTrendEA: это РЕАЛЬНЫЙ счёт. Проверьте советник на демо и включите InpAllowReal.");
      return INIT_FAILED;
   }

   string parts[];
   int n = StringSplit(InpSymbols, ',', parts);
   ArrayResize(g_syms, 0);
   for(int i = 0; i < n; i++)
   {
      string s = parts[i];
      StringTrimLeft(s); StringTrimRight(s);
      if(s == "") continue;
      string real = ResolveSymbol(s);
      if(real == "") { Print("символ не найден: ", s); continue; }
      int k = ArraySize(g_syms);
      ArrayResize(g_syms, k + 1);
      g_syms[k] = real;
   }
   int m = ArraySize(g_syms);
   if(m == 0) return INIT_PARAMETERS_INCORRECT;

   ArrayResize(g_hFast, m); ArrayResize(g_hSlow, m); ArrayResize(g_hTrend, m);
   ArrayResize(g_hAdx, m);  ArrayResize(g_hRsi, m);  ArrayResize(g_hAtr, m);
   ArrayResize(g_lastBar, m);
   for(int i = 0; i < m; i++)
   {
      string s = g_syms[i];
      g_hFast[i]  = iMA(s, InpTimeframe, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
      g_hSlow[i]  = iMA(s, InpTimeframe, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
      g_hTrend[i] = iMA(s, InpTimeframe, InpEmaTrend, 0, MODE_EMA, PRICE_CLOSE);
      g_hAdx[i]   = iADXWilder(s, InpTimeframe, InpAdxPeriod);
      g_hRsi[i]   = iRSI(s, InpTimeframe, InpRsiPeriod, PRICE_CLOSE);
      g_hAtr[i]   = iATR(s, InpTimeframe, InpAtrPeriod);
      g_lastBar[i]= 0;
      if(g_hFast[i] == INVALID_HANDLE || g_hSlow[i] == INVALID_HANDLE || g_hTrend[i] == INVALID_HANDLE ||
         g_hAdx[i] == INVALID_HANDLE || g_hRsi[i] == INVALID_HANDLE || g_hAtr[i] == INVALID_HANDLE)
         return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviationPts);
   g_disabled = GVGet("halted_forever", 0) > 0;
   EventSetTimer(10);
   Notify(StringFormat("запущен (%s), средства %.2f %s, символы: %s",
          demo ? "ДЕМО" : "РЕАЛ", AccountInfoDouble(ACCOUNT_EQUITY),
          AccountInfoString(ACCOUNT_CURRENCY), InpSymbols));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   for(int i = 0; i < ArraySize(g_syms); i++)
   {
      IndicatorRelease(g_hFast[i]); IndicatorRelease(g_hSlow[i]); IndicatorRelease(g_hTrend[i]);
      IndicatorRelease(g_hAdx[i]);  IndicatorRelease(g_hRsi[i]);  IndicatorRelease(g_hAtr[i]);
   }
}

void OnTick()  { Step(); }
void OnTimer() { Step(); }

//+------------------------------------------------------------------+
string ResolveSymbol(const string name)
{
   if(SymbolSelect(name, true)) return name;
   string best = "";
   for(int i = 0; i < SymbolsTotal(false); i++)
   {
      string s = SymbolName(i, false);
      if(StringFind(s, name) == 0 && (best == "" || StringLen(s) < StringLen(best))) best = s;
   }
   if(best != "") SymbolSelect(best, true);
   return best;
}

bool Buf(const int h, const int buffer, double &v1, double &v2)
{
   double b[];
   ArraySetAsSeries(b, true);
   if(CopyBuffer(h, buffer, 1, 2, b) != 2) return false;  // [0]=последний закрытый, [1]=предыдущий
   v1 = b[0]; v2 = b[1];
   return true;
}

int CountPositions(const string sym)
{
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(sym == "" || PositionGetString(POSITION_SYMBOL) == sym) c++;
   }
   return c;
}

void CloseAll(const string why, const string sym = "")
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(sym != "" && PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(!trade.PositionClose(t))
         Notify(StringFormat("не удалось закрыть #%I64u: %d", t, trade.ResultRetcode()));
   }
   if(sym == "") Print("закрыто всё: ", why);
}

double LotsForRisk(const string sym, const double slDist)
{
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(tickSize <= 0 || tickValue <= 0 || slDist <= 0 || step <= 0) return 0;
   double risk = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPct / 100.0;
   double raw  = risk / (slDist / tickSize * tickValue);
   double lots = MathFloor(raw / step + 1e-9) * step;
   lots = MathMin(lots, MathMin(vmax, InpMaxLot));
   if(lots < vmin) return 0;
   return NormalizeDouble(lots, 2);
}

//--- дневной стоп и аварийная остановка; значения хранятся в глобальных переменных терминала
bool RiskAllowsTrading()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   double today = dt.year * 10000 + dt.mon * 100 + dt.day;

   if(GVGet("day", 0) != today)
   {
      GlobalVariableSet(GV("day"), today);
      GlobalVariableSet(GV("day_eq"), eq);
      GlobalVariableSet(GV("halted_today"), 0);
   }
   double peak = MathMax(GVGet("peak", eq), eq);
   GlobalVariableSet(GV("peak"), peak);

   if(g_disabled) return false;
   if(peak > 0 && (peak - eq) / peak * 100.0 >= InpMaxDrawdownPct)
   {
      g_disabled = true;
      GlobalVariableSet(GV("halted_forever"), 1);
      CloseAll("просадка");
      Notify(StringFormat("АВАРИЙНАЯ ОСТАНОВКА: просадка %.1f%% от пика. Позиции закрыты.", (peak - eq) / peak * 100));
      return false;
   }
   double dayEq = GVGet("day_eq", eq);
   if(GVGet("halted_today", 0) > 0) return false;
   if(dayEq > 0 && (dayEq - eq) / dayEq * 100.0 >= InpMaxDailyLossPct)
   {
      GlobalVariableSet(GV("halted_today"), 1);
      CloseAll("дневной стоп");
      Notify(StringFormat("дневной стоп: %.1f%%. Позиции закрыты, торговля до завтра остановлена.", (dayEq - eq) / dayEq * 100));
      return false;
   }
   return true;
}

bool ScheduleAllowsEntry()
{
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return false;
   if(dt.hour < InpStartHourUtc || dt.hour >= InpEndHourUtc) return false;
   if(dt.day_of_week == 5 && dt.hour >= InpFridayNoNewUtc) return false;
   return true;
}

//+------------------------------------------------------------------+
void Step()
{
   bool canOpen = RiskAllowsTrading();

   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   if(InpFridayCloseUtc >= 0 && dt.day_of_week == 5 && dt.hour >= InpFridayCloseUtc && CountPositions("") > 0)
   {
      CloseAll("выходные");
      Notify("позиции закрыты перед выходными");
   }

   for(int i = 0; i < ArraySize(g_syms); i++)
      ProcessSymbol(i, canOpen);
}

void ProcessSymbol(const int i, const bool canOpen)
{
   string sym = g_syms[i];
   double f1, f2, s1, s2, tr1, tr2, adx1, adx2, rsi1, rsi2, atr1, atr2;
   if(!Buf(g_hFast[i], 0, f1, f2) || !Buf(g_hSlow[i], 0, s1, s2) || !Buf(g_hTrend[i], 0, tr1, tr2) ||
      !Buf(g_hAdx[i], 0, adx1, adx2) || !Buf(g_hRsi[i], 0, rsi1, rsi2) || !Buf(g_hAtr[i], 0, atr1, atr2))
      return;

   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double minDist = SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double eps = atr1 * 1e-6;

   //--- сопровождение: безубыток и трейлинг
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      ulong t = PositionGetTicket(k);
      if(t == 0 || PositionGetInteger(POSITION_MAGIC) != InpMagic || PositionGetString(POSITION_SYMBOL) != sym) continue;
      bool   isBuy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl   = PositionGetDouble(POSITION_SL);
      double tp   = PositionGetDouble(POSITION_TP);
      double price = isBuy ? bid : ask;
      double profit = isBuy ? price - open : open - price;
      double cand = sl;
      if(isBuy)
      {
         if(cand == 0) cand = -DBL_MAX;
         if(profit + eps >= InpBreakevenAtr * atr1) cand = MathMax(cand, open);
         if(InpTrailAtr > 0 && profit + eps >= InpTrailAtr * atr1) cand = MathMax(cand, price - InpTrailAtr * atr1);
         cand = NormalizeDouble(cand, digits);
         if(cand > sl + point / 2 && price - cand >= minDist) trade.PositionModify(t, cand, tp);
      }
      else
      {
         if(cand == 0) cand = DBL_MAX;
         if(profit + eps >= InpBreakevenAtr * atr1) cand = MathMin(cand, open);
         if(InpTrailAtr > 0 && profit + eps >= InpTrailAtr * atr1) cand = MathMin(cand, price + InpTrailAtr * atr1);
         cand = NormalizeDouble(cand, digits);
         if((sl == 0 || cand < sl - point / 2) && cand < DBL_MAX / 2 && cand - price >= minDist) trade.PositionModify(t, cand, tp);
      }
   }

   //--- сигналы — только один раз на новом закрытом баре
   datetime bar = iTime(sym, InpTimeframe, 1);
   if(bar == 0 || bar == g_lastBar[i]) return;
   g_lastBar[i] = bar;
   if(Bars(sym, InpTimeframe) < InpEmaTrend + 50) return;

   double close1 = iClose(sym, InpTimeframe, 1);
   bool crossUp   = f1 > s1 && !(f2 > s2);
   bool crossDown = !(f1 > s1) && f2 > s2;
   bool buy  = crossUp   && close1 > tr1 && adx1 >= InpAdxMin && rsi1 < InpRsiOverbought;
   bool sell = crossDown && close1 < tr1 && adx1 >= InpAdxMin && rsi1 > InpRsiOversold;

   //--- выход по обратному пересечению
   if(InpExitOnOpposite)
   {
      for(int k = PositionsTotal() - 1; k >= 0; k--)
      {
         ulong t = PositionGetTicket(k);
         if(t == 0 || PositionGetInteger(POSITION_MAGIC) != InpMagic || PositionGetString(POSITION_SYMBOL) != sym) continue;
         bool isBuy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
         if((isBuy && crossDown) || (!isBuy && crossUp))
         {
            double pl = PositionGetDouble(POSITION_PROFIT);
            if(trade.PositionClose(t)) Notify(StringFormat("%s: выход по обратному сигналу, P/L %.2f", sym, pl));
         }
      }
   }

   if(!(buy || sell) || !canOpen || !ScheduleAllowsEntry()) return;
   if(CountPositions(sym) > 0 || CountPositions("") >= InpMaxPositions) return;
   if(SymbolInfoInteger(sym, SYMBOL_SPREAD) > InpMaxSpreadPts) { Print(sym, ": широкий спред, вход пропущен"); return; }

   trade.SetTypeFillingBySymbol(sym);
   double entry = buy ? ask : bid;
   double slv = buy ? entry - InpSlAtr * atr1 : entry + InpSlAtr * atr1;
   double tpv = buy ? entry + InpTpAtr * atr1 : entry - InpTpAtr * atr1;
   if(MathAbs(entry - slv) < minDist) return;
   double lots = LotsForRisk(sym, MathAbs(entry - slv));
   if(lots <= 0) { Print(sym, ": объём меньше минимального для заданного риска"); return; }
   slv = NormalizeDouble(slv, digits);
   tpv = NormalizeDouble(tpv, digits);

   bool ok = buy ? trade.Buy(lots, sym, entry, slv, tpv, "AlfaTrend")
                 : trade.Sell(lots, sym, entry, slv, tpv, "AlfaTrend");
   if(ok)
      Notify(StringFormat("%s %s %.2f лот @%s SL %s TP %s", buy ? "BUY" : "SELL", sym, lots,
             DoubleToString(entry, digits), DoubleToString(slv, digits), DoubleToString(tpv, digits)));
   else
      Notify(StringFormat("%s: ордер отклонён, код %d %s", sym, trade.ResultRetcode(), trade.ResultRetcodeDescription()));
}
//+------------------------------------------------------------------+
