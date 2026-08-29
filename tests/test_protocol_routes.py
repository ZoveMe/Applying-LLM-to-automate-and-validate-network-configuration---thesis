"""Route facts for dynamically routed labs.

With static routing a fact names the next hop. With OSPF the next hop is not
fixed — more than one path exists and the protocol chooses — so the fact names
the protocol instead. These tests pin down what "learned by OSPF" must mean.
"""
import unittest

from validation.dynamic_validate import route_learned_by

OSPF_SELECTED = (
    "Routing entry for 10.10.10.0/24\n"
    "O>* 10.10.10.0/24 [110/20] via 10.0.0.6, eth2, weight 1, 00:02:11\n"
)
# OSPF knows the prefix but a static route won selection: traffic does not
# follow OSPF, so the fact must fail.
OSPF_NOT_SELECTED = (
    "O   10.10.10.0/24 [110/20] via 10.0.0.6, eth2 inactive\n"
    "S>* 10.10.10.0/24 [1/0] via 10.0.0.6, eth2\n"
)
STATIC_ONLY = "S>* 10.10.10.0/24 [1/0] via 10.0.0.6, eth2, weight 1\n"
CONNECTED = "C>* 10.30.30.0/24 is directly connected, eth2\n"
ABSENT = "% Network not in table\n"


class ProtocolRouteFacts(unittest.TestCase):
    def test_selected_ospf_route_counts(self):
        self.assertTrue(route_learned_by(OSPF_SELECTED, "ospf"))

    def test_static_route_is_not_ospf(self):
        """A hand-placed route must not satisfy a fact about the protocol."""
        self.assertFalse(route_learned_by(STATIC_ONLY, "ospf"))

    def test_known_but_unselected_ospf_route_fails(self):
        """Knowing a prefix is not the same as carrying traffic to it."""
        self.assertFalse(route_learned_by(OSPF_NOT_SELECTED, "ospf"))

    def test_absent_route_fails(self):
        self.assertFalse(route_learned_by(ABSENT, "ospf"))

    def test_connected_route_is_not_ospf(self):
        self.assertFalse(route_learned_by(CONNECTED, "ospf"))

    def test_other_protocols_recognised(self):
        self.assertTrue(route_learned_by(STATIC_ONLY, "static"))
        self.assertTrue(route_learned_by(CONNECTED, "connected"))

    def test_unknown_protocol_is_refused(self):
        """A typo in the intent must not silently pass the check."""
        with self.assertRaises(ValueError):
            route_learned_by(OSPF_SELECTED, "eigrp")


# Real output captured from the enterprise lab. Asking for a single prefix
# returns the detailed form, not the table form the first version assumed.
DETAIL_OSPF_BEST = (
    "Routing entry for 10.10.10.0/24\n"
    '  Known via "ospf", distance 110, metric 20, best\n'
    "  Last update 00:00:33 ago\n"
    "  * 10.0.0.6, via eth2, weight 1\n"
)
DETAIL_OSPF_TWO_PATHS = (
    "Routing entry for 10.10.10.0/24\n"
    '  Known via "ospf", distance 110, metric 30, best\n'
    "  Last update 00:00:20 ago\n"
    "  * 10.0.0.13, via eth1, weight 1\n"
    "  * 10.0.0.17, via eth2, weight 1\n"
)
# Known but another protocol won: no traffic follows it.
DETAIL_OSPF_NOT_BEST = (
    "Routing entry for 10.10.10.0/24\n"
    '  Known via "ospf", distance 110, metric 20\n'
    "  Last update 00:01:02 ago\n"
    '  Known via "static", distance 1, metric 0, best\n'
    "  * 10.0.0.6, via eth2, weight 1\n"
)
DETAIL_CONNECTED = (
    "Routing entry for 10.30.30.0/24\n"
    '  Known via "connected", distance 0, metric 0, best\n'
    "  * directly connected, eth2\n"
)


class DetailedRouteOutput(unittest.TestCase):
    """`show ip route <prefix>` prints words, not the single-letter codes."""

    def test_detailed_ospf_entry_counts(self):
        self.assertTrue(route_learned_by(DETAIL_OSPF_BEST, "ospf"))

    def test_equal_cost_paths_count(self):
        """Two next hops through a redundant core is still one OSPF route."""
        self.assertTrue(route_learned_by(DETAIL_OSPF_TWO_PATHS, "ospf"))

    def test_ospf_known_but_not_best_fails(self):
        self.assertFalse(route_learned_by(DETAIL_OSPF_NOT_BEST, "ospf"))

    def test_detailed_connected_is_not_ospf(self):
        self.assertFalse(route_learned_by(DETAIL_CONNECTED, "ospf"))

    def test_detailed_connected_recognised_as_connected(self):
        self.assertTrue(route_learned_by(DETAIL_CONNECTED, "connected"))


if __name__ == "__main__":
    unittest.main()
