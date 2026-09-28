"""SimTrade CLI for launching the server, running demos, and managing datasets."""

import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
from typing import List, Optional
import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from simtrade.config import MarginConfig, MatchingConfig, ServerConfig, SimulationConfig
from simtrade.engine.feeder import SyntheticDataGenerator
from simtrade.server.app import create_app

app = typer.Typer(help="SimTrade Paper Trading Simulator Server CLI")
console = Console()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host interface to bind"),
    port: int = typer.Option(8000, help="Port to listen on"),
    speed: float = typer.Option(10.0, help="Simulation playback speed multiplier (1=realtime, 60=1s/min, 0=max)"),
    tickers: str = typer.Option("AAPL,NVDA,TSLA,MSFT", help="Comma-separated tickers (or 'ALL')"),
    data_file: Optional[str] = typer.Option(None, help="Path to historical parquet or CSV file (defaults to data/1m_20260817_now.parquet if exists)"),
    data_dir: Optional[str] = typer.Option("data", help="Directory containing historical Parquet/CSV data"),
    leverage: float = typer.Option(2.0, help="Max leverage allowed"),
    maint_margin: float = typer.Option(0.25, help="Maintenance margin rate (e.g. 0.25 for 25%)"),
):
    """Start the SimTrade paper trading server with WebSocket, REST API, and web dashboard."""
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    server_cfg = ServerConfig(host=host, port=port)
    sim_cfg = SimulationConfig(
        speed_multiplier=speed,
        tickers=ticker_list,
        data_file=data_file,
        data_dir=data_dir,
    )
    margin_cfg = MarginConfig(max_leverage=leverage, maintenance_margin_rate=maint_margin)
    matching_cfg = MatchingConfig()

    console.print(f"[bold green]Starting SimTrade Paper Trading Server[/bold green]")
    console.print(f" • Dashboard: [cyan]http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/dashboard[/cyan]")
    console.print(f" • API Docs:  [cyan]http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/docs[/cyan]")
    console.print(f" • Tickers:   [yellow]{', '.join(ticker_list)}[/yellow]")
    console.print(f" • Speed:     [magenta]{speed}x[/magenta]")

    fastapi_app = create_app(
        server_config=server_cfg,
        sim_config=sim_cfg,
        matching_config=matching_cfg,
        margin_config=margin_cfg,
    )

    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")


@app.command("generate-data")
def generate_sample_data(
    output_dir: str = typer.Option("data", help="Output directory for generated CSV files"),
    days: int = typer.Option(2, help="Number of trading days to simulate"),
    tickers: str = typer.Option("AAPL,NVDA,TSLA,MSFT", help="Comma-separated ticker list"),
):
    """Generate realistic synthetic 1-minute OHLCV historical CSV files."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    console.print(f"Generating synthetic 1-minute data for {ticker_list} ({days} days)...")
    gen = SyntheticDataGenerator(ticker_list)
    start_dt = datetime(2026, 1, 5, 9, 30, 0)
    
    # 390 trading minutes per day
    total_minutes = days * 390
    for ticker in ticker_list:
        rows = []
        cur_dt = start_dt
        for m in range(total_minutes):
            bar = gen.generate_bar(ticker, cur_dt)
            rows.append({
                "timestamp": bar.timestamp.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "vwap": bar.vwap,
            })
            cur_dt += timedelta(minutes=1)

        import pandas as pd
        df = pd.DataFrame(rows)
        csv_file = out_path / f"{ticker}_1m.csv"
        df.to_csv(csv_file, index=False)
        console.print(f"Saved {len(df)} 1m bars to [green]{csv_file}[/green]")

    console.print("[bold green]Data generation complete![/bold green]")


def main():
    app()


if __name__ == "__main__":
    main()
