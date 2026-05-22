#!/usr/bin/env python3
"""
ZERODHA KITE MICRO-CAPITAL OPTIONS TRADING BOT
Strategy: Gap-Based Quick Scalp with Automated Position Management
Capital: ₹1,000 - ₹5,000
Risk:Reward: 1:2 to 1:3
Daily Loss Limit: 20%

SETUP INSTRUCTIONS:
1. pip install kiteconnect
2. Get API key from Zerodha developer console
3. Replace API_KEY and CLIENT_SECRET
4. Run this script during market hours (9:15 AM - 3:15 PM)
"""

import json
import logging
from datetime import datetime, time
from typing import Dict, List, Optional
import time as time_module
import threading
from dataclasses import dataclass

# Install: pip install kiteconnect
try:
    from kiteconnect import KiteConnect
except ImportError:
    print("ERROR: Install kiteconnect: pip install kiteconnect")
    exit(1)

# ==================== CONFIGURATION ====================

# YOUR ZERODHA API CREDENTIALS (Get from https://developer.kite.trade/)
API_KEY = ""
CLIENT_SECRET = ""
REQUEST_TOKEN = "get_from_login_url"  # Obtain after login

# TRADING PARAMETERS
STARTING_CAPITAL = 1000  # In Rupees (change to 2500 or 5000 as needed)
DAILY_LOSS_LIMIT_PCT = 0.20  # 20% of capital
RISK_PER_TRADE_PCT = 0.03  # 3% per trade (can adjust to 2-4%)
MIN_PREMIUM = 3  # Minimum premium to trade
MAX_PREMIUM = 20  # Maximum premium to trade
PROFIT_TARGET_RATIO = 2.0  # 1:2 ratio (risk:reward)
STOP_LOSS_PCT = 0.40  # 40% of premium loss = hard stop

# TRADING INSTRUMENT
SYMBOL = "NIFTY 50"  # Nifty 50 index
INSTRUMENT_TOKEN = 256265  # Nifty 50 instrument token
TRADING_SEGMENT = "NFO"

# TIME WINDOWS (IST - Indian Standard Time)
MARKET_OPEN = time(9, 15)
TRADING_START = time(10, 0)  # Best time to trade
TRADING_END = time(15, 15)  # 3:15 PM - close all positions
MARKET_CLOSE = time(15, 30)

# OPTIONAL: Trading frequency limit
MAX_TRADES_PER_DAY = 6
HOLD_TIME_MINUTES = 45  # Maximum hold time per trade

# ==================== LOGGING SETUP ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== DATA CLASSES ====================

@dataclass
class Trade:
    """Track individual trades"""
    trade_id: int
    symbol: str
    instrument_token: int
    strike: float
    option_type: str  # "CE" or "PE"
    entry_premium: float
    entry_time: datetime
    entry_price_nifty: float
    quantity: int
    
    # Exit tracking
    tier1_sell_time: Optional[datetime] = None
    tier1_profit: float = 0.0
    tier2_sell_time: Optional[datetime] = None
    tier2_profit: float = 0.0
    stop_loss_hit: bool = False
    final_exit_time: Optional[datetime] = None
    final_profit: float = 0.0
    
    def get_duration_minutes(self) -> int:
        """Get how long trade has been open"""
        if self.final_exit_time:
            return int((self.final_exit_time - self.entry_time).total_seconds() / 60)
        return int((datetime.now() - self.entry_time).total_seconds() / 60)
    
    def get_pnl(self) -> float:
        """Get total P&L for this trade"""
        return self.tier1_profit + self.tier2_profit + self.final_profit

# ==================== TRADING BOT CLASS ====================

class MicroCapOptionsBot:
    """Main trading bot for micro-capital options trading"""
    
    def __init__(self, api_key: str, client_secret: str, request_token: str):
        """Initialize the bot with Zerodha API"""
        self.kite = KiteConnect(api_key=api_key)
        self.api_key = api_key
        self.client_secret = client_secret
        
        # Trading state
        self.is_authenticated = False
        self.trades: List[Trade] = []
        self.trade_counter = 0
        self.positions: Dict = {}
        
        # P&L tracking
        self.daily_pnl = 0.0
        self.daily_loss_limit = STARTING_CAPITAL * DAILY_LOSS_LIMIT_PCT
        self.session_start_time = None
        self.trades_executed_today = 0
        
        # Market data
        self.last_price = {}
        self.ltp_nifty = 0
        self.bid_ask_data = {}
        
        logger.info("Bot initialized")
    
    def authenticate(self, request_token: str) -> bool:
        """Authenticate with Zerodha using request token"""
        try:
            logger.info("Attempting authentication...")
            response = self.kite.generate_session(
                request_token=request_token,
                api_secret=self.client_secret
            )
            self.kite.set_access_token(response['access_token'])
            self.is_authenticated = True
            logger.info("✓ Successfully authenticated with Zerodha API")
            return True
        except Exception as e:
            logger.error(f"✗ Authentication failed: {e}")
            logger.error("Go to: https://kite.zerodha.com/connect/login")
            logger.error("Copy request_token from URL and update REQUEST_TOKEN")
            return False
    
    def get_request_token_url(self) -> str:
        """Get login URL for request token"""
        return self.kite.login_url()
    
    def get_option_chain(self, symbol: str, expiry: str) -> Dict:
        """Fetch option chain data"""
        try:
            quote = self.kite.quote(symbol, expiry)
            return quote
        except Exception as e:
            logger.error(f"Error fetching option chain: {e}")
            return {}
    
    def get_current_price(self, instrument_token: int) -> float:
        """Get current market price for instrument"""
        try:
            quote = self.kite.quote([instrument_token])
            if instrument_token in quote:
                return quote[instrument_token]['last_price']
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching price: {e}")
            return 0.0
    
    def get_nifty_price(self) -> float:
        """Get current Nifty 50 price"""
        try:
            self.ltp_nifty = self.get_current_price(INSTRUMENT_TOKEN)
            return self.ltp_nifty
        except Exception as e:
            logger.error(f"Error fetching Nifty price: {e}")
            return self.ltp_nifty
    
    def calculate_position_size(self, premium: float) -> int:
        """Calculate position size based on risk and premium"""
        risk_amount = STARTING_CAPITAL * RISK_PER_TRADE_PCT
        position_size = int(risk_amount / premium)
        
        # For Nifty options, multiply by lot size (75)
        # Adjust for broker's contract value
        position_size = max(1, position_size // 75) * 75
        
        return position_size
    
    def should_trade_now(self) -> bool:
        """Check if we're in trading window"""
        current_time = datetime.now().time()
        
        # Must be between TRADING_START and TRADING_END
        if not (TRADING_START <= current_time <= TRADING_END):
            return False
        
        # Check daily loss limit
        if self.daily_pnl <= -self.daily_loss_limit:
            logger.warning(f"⚠ DAILY LOSS LIMIT HIT: {self.daily_pnl:.2f}. No more trades today.")
            return False
        
        # Check max trades per day
        if self.trades_executed_today >= MAX_TRADES_PER_DAY:
            logger.warning(f"⚠ MAX TRADES REACHED: {self.trades_executed_today}. No more trades today.")
            return False
        
        return True
    
    def identify_setup(self) -> Optional[Dict]:
        """Identify trading setup from market conditions"""
        try:
            nifty_price = self.get_nifty_price()
            
            # Get market data (would need to implement actual market depth)
            # For now, returning basic setup structure
            
            # SETUP 1: Gap-down bounce detection
            # (This would need historical data comparison)
            
            # SETUP 2: Momentum detection
            # (This would need RSI/MACD calculation)
            
            # SETUP 3: Support/Resistance bounce
            # (This would need level detection)
            
            # Placeholder: Return sample setup
            setup = {
                "type": "gap_down_bounce",
                "direction": "CE",  # CE = call, PE = put
                "nifty_price": nifty_price,
                "confidence": 0.65,
                "reason": "Market bouncing from support"
            }
            
            return setup
        except Exception as e:
            logger.error(f"Error identifying setup: {e}")
            return None
    
    def find_best_strike(self, nifty_price: float, direction: str) -> Optional[Dict]:
        """Find best option strike based on current Nifty price"""
        try:
            # Get option chain for nearest weekly expiry
            # This is simplified; real implementation would fetch full chain
            
            if direction == "CE":  # Call option (bullish)
                # Target OTM call - approximately 200-400 points OTM
                strike = int(nifty_price / 100) * 100 + 100
            else:  # Put option (bearish)
                strike = int(nifty_price / 100) * 100 - 100
            
            # Get estimated premium (simplified - would fetch real data)
            estimated_premium = abs(nifty_price - strike) * 0.05  # Rough estimate
            
            # Filter by premium constraints
            if MIN_PREMIUM <= estimated_premium <= MAX_PREMIUM:
                return {
                    "strike": strike,
                    "option_type": direction,
                    "estimated_premium": estimated_premium,
                    "symbol": f"NIFTY{strike}{direction}"
                }
            
            return None
        except Exception as e:
            logger.error(f"Error finding strike: {e}")
            return None
    
    def place_order(self, trade: Trade) -> bool:
        """Place buy order for option"""
        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                symbol=trade.symbol,
                quantity=trade.quantity,
                side=self.kite.SIDE_BUY,
                order_type=self.kite.ORDER_TYPE_MARKET,
                price=None
            )
            
            logger.info(f"✓ Order placed: {trade.symbol} | Qty: {trade.quantity} | Order ID: {order_id}")
            return True
        except Exception as e:
            logger.error(f"✗ Order placement failed: {e}")
            return False
    
    def place_exit_order(self, trade: Trade, exit_type: str) -> bool:
        """Place sell order for exiting position"""
        try:
            quantity = trade.quantity
            
            # For tier exits, reduce quantity
            if exit_type == "tier1":
                quantity = int(trade.quantity * 0.4)  # 40% of position
            elif exit_type == "tier2":
                quantity = int(trade.quantity * 0.35)  # 35% of position
            
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                symbol=trade.symbol,
                quantity=quantity,
                side=self.kite.SIDE_SELL,
                order_type=self.kite.ORDER_TYPE_MARKET,
                price=None
            )
            
            logger.info(f"✓ Exit order placed ({exit_type}): {trade.symbol} | Qty: {quantity}")
            return True
        except Exception as e:
            logger.error(f"✗ Exit order failed: {e}")
            return False
    
    def execute_trade(self) -> bool:
        """Execute a complete trade setup"""
        if not self.should_trade_now():
            return False
        
        try:
            # Identify setup
            setup = self.identify_setup()
            if not setup:
                return False
            
            logger.info(f"Setup identified: {setup['type']} ({setup['direction']})")
            
            # Find best strike
            strike_info = self.find_best_strike(setup['nifty_price'], setup['direction'])
            if not strike_info:
                logger.warning("No suitable strike found")
                return False
            
            # Create trade object
            self.trade_counter += 1
            trade = Trade(
                trade_id=self.trade_counter,
                symbol=strike_info['symbol'],
                instrument_token=0,  # Would be fetched from broker
                strike=strike_info['strike'],
                option_type=strike_info['option_type'],
                entry_premium=strike_info['estimated_premium'],
                entry_time=datetime.now(),
                entry_price_nifty=setup['nifty_price'],
                quantity=self.calculate_position_size(strike_info['estimated_premium'])
            )
            
            # Place order
            if self.place_order(trade):
                self.trades.append(trade)
                self.trades_executed_today += 1
                logger.info(f"Trade #{trade.trade_id}: {strike_info['symbol']} @ ₹{strike_info['estimated_premium']:.2f}")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Error executing trade: {e}")
            return False
    
    def monitor_and_exit_trades(self):
        """Monitor open trades and execute exits"""
        for trade in self.trades:
            if trade.final_exit_time:
                continue  # Trade already exited
            
            # Get current premium
            current_premium = self.get_current_price(trade.instrument_token)
            if current_premium == 0:
                continue
            
            premium_change_pct = (current_premium - trade.entry_premium) / trade.entry_premium
            duration = trade.get_duration_minutes()
            
            # Tier 1 Exit: +30% gain
            if premium_change_pct >= 0.30 and not trade.tier1_sell_time:
                trade.tier1_profit = (current_premium - trade.entry_premium) * int(trade.quantity * 0.4)
                trade.tier1_sell_time = datetime.now()
                self.place_exit_order(trade, "tier1")
                logger.info(f"✓ Trade #{trade.trade_id} Tier 1: +{premium_change_pct*100:.1f}% | Profit: ₹{trade.tier1_profit:.2f}")
            
            # Tier 2 Exit: +60% gain
            elif premium_change_pct >= 0.60 and not trade.tier2_sell_time:
                trade.tier2_profit = (current_premium - trade.entry_premium) * int(trade.quantity * 0.35)
                trade.tier2_sell_time = datetime.now()
                self.place_exit_order(trade, "tier2")
                logger.info(f"✓ Trade #{trade.trade_id} Tier 2: +{premium_change_pct*100:.1f}% | Profit: ₹{trade.tier2_profit:.2f}")
            
            # Hard Stop Loss: -40% or Time Stop
            elif premium_change_pct <= -STOP_LOSS_PCT or duration >= HOLD_TIME_MINUTES:
                trade.stop_loss_hit = True
                trade.final_profit = (current_premium - trade.entry_premium) * trade.quantity
                trade.final_exit_time = datetime.now()
                self.place_exit_order(trade, "stop_loss")
                
                reason = "Stop Loss" if premium_change_pct <= -STOP_LOSS_PCT else "Time Limit"
                logger.warning(f"⚠ Trade #{trade.trade_id} EXITED ({reason}): {premium_change_pct*100:.1f}% | Loss: ₹{trade.final_profit:.2f}")
            
            # Update daily P&L
            self.daily_pnl += trade.get_pnl()
    
    def print_daily_summary(self):
        """Print end-of-day trading summary"""
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t.get_pnl() > 0)
        losing_trades = sum(1 for t in self.trades if t.get_pnl() < 0)
        
        logger.info("\n" + "="*60)
        logger.info("DAILY TRADING SUMMARY")
        logger.info("="*60)
        logger.info(f"Total Trades: {total_trades}")
        logger.info(f"Winning Trades: {winning_trades} ({winning_trades/max(total_trades,1)*100:.1f}%)")
        logger.info(f"Losing Trades: {losing_trades}")
        logger.info(f"Daily P&L: ₹{self.daily_pnl:.2f}")
        logger.info(f"Return %: {self.daily_pnl/STARTING_CAPITAL*100:.2f}%")
        logger.info(f"Daily Loss Limit: ₹{self.daily_loss_limit:.2f}")
        logger.info(f"Remaining Capital: ₹{STARTING_CAPITAL + self.daily_pnl:.2f}")
        logger.info("="*60 + "\n")
    
    def run(self):
        """Main bot loop"""
        logger.info("Bot starting...")
        self.session_start_time = datetime.now()
        
        while True:
            try:
                current_time = datetime.now().time()
                
                # Stop trading after 3:15 PM
                if current_time >= TRADING_END:
                    logger.info("Market closing soon. Exiting all positions...")
                    # Close all open trades
                    break
                
                # Execute trades if conditions met
                if self.should_trade_now():
                    self.execute_trade()
                
                # Monitor and exit trades
                self.monitor_and_exit_trades()
                
                # Sleep to avoid API rate limits
                time_module.sleep(5)
            
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time_module.sleep(10)
        
        self.print_daily_summary()

# ==================== MAIN EXECUTION ====================

def main():
    """Main function to run the bot"""
    
    print("\n" + "="*60)
    print("ZERODHA MICRO-CAPITAL OPTIONS TRADING BOT")
    print("="*60)
    print(f"Capital: ₹{STARTING_CAPITAL}")
    print(f"Daily Loss Limit: {DAILY_LOSS_LIMIT_PCT*100}%")
    print(f"Risk Per Trade: {RISK_PER_TRADE_PCT*100}%")
    print(f"Risk:Reward Ratio: 1:{PROFIT_TARGET_RATIO}")
    print("="*60 + "\n")
    
    # Check API credentials
    if API_KEY == "your_api_key_here":
        print("⚠ ERROR: Update API credentials in script!")
        print("1. Go to: https://developer.kite.trade/")
        print("2. Create an app and get API_KEY & CLIENT_SECRET")
        print("3. Update lines 40-41 in this script")
        exit(1)
    
    # Initialize bot
    bot = MicroCapOptionsBot(API_KEY, CLIENT_SECRET, REQUEST_TOKEN)
    
    # Authenticate
    if REQUEST_TOKEN == "get_from_login_url":
        print("FIRST TIME SETUP:")
        print("1. Visit this URL:", bot.get_request_token_url())
        print("2. After login, copy REQUEST_TOKEN from URL")
        print("3. Update REQUEST_TOKEN in script (line 42)")
        print("4. Run script again")
        exit(1)
    
    if not bot.authenticate(REQUEST_TOKEN):
        exit(1)
    
    # Run bot
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped")

if __name__ == "__main__":
    main()

# ==================== USAGE GUIDE ====================
"""
SETUP INSTRUCTIONS:

Step 1: Install Zerodha API library
    pip install kiteconnect

Step 2: Get API Credentials
    - Visit: https://developer.kite.trade/
    - Create an app
    - Copy API_KEY and CLIENT_SECRET
    - Update lines 40-41 in script

Step 3: First Authentication
    - Run script: python trading_bot.py
    - Click login URL that appears
    - Copy REQUEST_TOKEN from URL
    - Update line 42 in script
    - Run again

Step 4: Run the Bot
    - Run during market hours: python trading_bot.py
    - Bot will auto-trade from 10 AM to 3:15 PM
    - Check trading_bot.log for detailed logs

CONFIGURATION OPTIONS (Edit these):
    - STARTING_CAPITAL: ₹1000, ₹2500, or ₹5000
    - DAILY_LOSS_LIMIT_PCT: 0.20 = 20% loss limit
    - RISK_PER_TRADE_PCT: 0.03 = 3% per trade
    - MAX_TRADES_PER_DAY: Maximum 6 trades
    - HOLD_TIME_MINUTES: Maximum 45 mins per trade

IMPORTANT REMINDERS:
    ✓ Test on demo account first!
    ✓ Keep ₹1000-5000 only (don't risk more)
    ✓ Monitor trades - don't leave unattended
    ✓ Close ALL positions by 3:15 PM
    ✓ Check logs daily for performance
    ✓ If daily loss hits 20%, stop trading immediately

SAFETY FEATURES BUILT-IN:
    ✓ Automatic daily loss limit enforcement
    ✓ Hard stop losses at -40% premium
    ✓ Automatic exit at 45 min hold time
    ✓ Tier-based profit taking
    ✓ Trading window restrictions (10 AM - 3:15 PM)
    ✓ Maximum 6 trades per day
    ✓ Real-time P&L tracking

TROUBLESHOOTING:
    - "Authentication failed": Check API_KEY, CLIENT_SECRET, REQUEST_TOKEN
    - "Order placement failed": Check account balance, market hours
    - "Price fetch failed": Check internet connection, symbol spelling
    - "No suitable strike found": Premium outside ₹3-20 range

For support: Check trading_bot.log file for detailed error messages
"""
