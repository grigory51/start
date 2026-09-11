from __future__ import annotations

import unittest

from cli.commands import listening_ports, network_processes


LSOF_OUTPUT = """p123
cPython
f7
PTCP
n127.0.0.1:8080
TST=LISTEN
f8
PUDP
n*:5353
p456
cnode
f12
PTCP
n[::1]:3000-> [::1]:52341
TST=ESTABLISHED
"""

NETTOP_OUTPUT = """,interface,state,bytes_in,bytes_out
GoLand.123,en0,ESTABLISHED,120,240
tcp4 10.0.0.2:50400<->1.1.1.1:443,en0,ESTABLISHED,40,80
python.456,lo0,,30,40
"""


class FakeContext:
    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[tuple[str, ...], tuple[int, ...]]] = []

    async def run(
        self, *argv: str, allowed_codes: tuple[int, ...] = (0,)
    ) -> str:
        self.calls.append((argv, allowed_codes))
        return self.outputs.pop(0)


class TaskProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_lsof_listening_keeps_tcp_listen_and_udp(self) -> None:
        snapshot = listening_ports.parse_lsof_listening(LSOF_OUTPUT)
        self.assertEqual(snapshot.columns, listening_ports.LISTENING_COLUMNS)
        self.assertEqual(
            snapshot.rows,
            [
                ("123", "Python", "TCP", "127.0.0.1", "8080"),
                ("123", "Python", "UDP", "*", "5353"),
            ],
        )

    def test_parse_lsof_listening_tolerates_empty_and_malformed_fields(self) -> None:
        self.assertEqual(listening_ports.parse_lsof_listening("").rows, [])
        self.assertEqual(listening_ports.parse_lsof_listening("not field output\nf").rows, [])

    def test_parse_nettop_csv_uses_leading_identity_column_and_connection_rows(self) -> None:
        snapshot = network_processes.parse_nettop_csv(NETTOP_OUTPUT)
        self.assertEqual(snapshot.columns, network_processes.NETWORK_COLUMNS)
        self.assertEqual(
            snapshot.rows,
            [
                ("123", "GoLand", "en0", "", "ESTABLISHED", "120", "240"),
                (
                    "123", "GoLand", "en0", "tcp4 10.0.0.2:50400<->1.1.1.1:443",
                    "ESTABLISHED", "40", "80",
                ),
                ("456", "python", "lo0", "", "", "30", "40"),
            ],
        )

    def test_parse_nettop_csv_handles_empty_and_rejects_bad_header(self) -> None:
        self.assertEqual(network_processes.parse_nettop_csv("").rows, [])
        with self.assertRaisesRegex(ValueError, "заголовок interface/state/bytes"):
            network_processes.parse_nettop_csv("not,csv\n")

    def test_connection_with_dot_port_does_not_replace_process(self) -> None:
        output = NETTOP_OUTPUT.replace("1.1.1.1:443", "1.1.1.1.443")
        row = network_processes.parse_nettop_csv(output).rows[1]
        self.assertEqual(row[:2], ("123", "GoLand"))
        self.assertIn("<->", row[3])

    async def test_listening_ports_builds_lsof_arguments_and_accepts_empty_result(self) -> None:
        context = FakeContext(LSOF_OUTPUT)
        snapshot = await listening_ports.snapshot(
            context, {"protocol": "tcp", "port": "8080"}
        )
        self.assertEqual(len(snapshot.rows), 2)
        self.assertEqual(
            context.calls,
            [
                (
                    ("lsof", "-nP", "-FpcfPnT", "-iTCP:8080", "-sTCP:LISTEN"),
                    (0, 1),
                )
            ],
        )

    async def test_listening_ports_rejects_invalid_port_before_command(self) -> None:
        context = FakeContext()
        with self.assertRaisesRegex(ValueError, "1 до 65535"):
            await listening_ports.snapshot(context, {"port": "65536"})
        self.assertEqual(context.calls, [])

    async def test_listening_ports_queries_tcp_and_udp_separately_for_all(self) -> None:
        context = FakeContext("", "")
        await listening_ports.snapshot(context, {"protocol": "all"})
        self.assertEqual(
            [arguments[3] for arguments, _ in context.calls],
            ["-iTCP", "-iUDP"],
        )

    async def test_network_processes_builds_single_csv_sample(self) -> None:
        context = FakeContext(NETTOP_OUTPUT)
        snapshot = await network_processes.snapshot(
            context, {"protocol": "udp"}
        )
        self.assertEqual(len(snapshot.rows), 3)
        self.assertEqual(
            context.calls,
            [
                (
                    (
                        "nettop", "-L", "1", "-n", "-x", "-J",
                        "interface,state,bytes_in,bytes_out",
                        "-m", "udp",
                    ),
                    (0,),
                )
            ],
        )

    async def test_network_processes_omits_empty_application_and_all_mode(self) -> None:
        context = FakeContext("")
        await network_processes.snapshot(
            context, {"protocol": "all", "application": ""}
        )
        arguments, _ = context.calls[0]
        self.assertNotIn("-m", arguments)
        self.assertNotIn("-p", arguments)


if __name__ == "__main__":
    unittest.main()
