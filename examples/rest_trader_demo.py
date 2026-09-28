"""Simple REST client interacting with SimTrade server."""

import httpx

BASE_URL = "http://127.0.0.1:8000/api/v1"


def main():
    with httpx.Client() as client:
        # Check server status
        status = client.get(f"{BASE_URL}/sim/status").json()
        print("Server Status:", status)

        # Get latest market bars
        bars = client.get(f"{BASE_URL}/market/bars/latest").json()
        print("Latest Bars:", list(bars.keys()))

        # Submit a limit buy order for 50 shares of AAPL
        order_payload = {
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 50,
            "limit_price": 220.0,
            "time_in_force": "GTC"
        }
        resp = client.post(f"{BASE_URL}/orders", json=order_payload)
        print("Order Submitted:", resp.status_code, resp.json())

        # Advance 1 step
        step_resp = client.post(f"{BASE_URL}/sim/step").json()
        print("Sim Clock Advanced to:", step_resp["timestamp"])

        # Check account
        acc = client.get(f"{BASE_URL}/account").json()
        print(f"Account Equity: ${acc['equity']:.2f}, Cash: ${acc['cash']:.2f}")


if __name__ == "__main__":
    main()
