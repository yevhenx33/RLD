from astrid_node import Processor


class HealthFactorAlerts(Processor):
    inputs = ["aave.processed.account_profiles.v1"]

    def handle(self, msg, ctx):
        if float(msg.get("health_factor", 999)) < 1.25:
            ctx.insert(
                "astrid_user.aave_health_factor_alerts",
                {
                    "user": msg["user"],
                    "timestamp": msg["timestamp"],
                    "health_factor": msg["health_factor"],
                },
            )


if __name__ == "__main__":
    HealthFactorAlerts.run()
