"""Construction log capture keeps evidence bounded and free of formatted data."""
import logging
import unittest

from adkl import construction_log


class TestConstructionLog(unittest.TestCase):
    def test_captures_isolated_loggers_and_restores_logging(self):
        isolated = logging.getLogger("adkl.test.isolated")
        isolated.propagate = False
        self.addCleanup(setattr, isolated, "propagate", True)
        original = logging.Logger.handle
        with construction_log.capture() as log:
            isolated.error("startup_failed lane=%s", "native")
            logging.getLogger("adkl.test").info("ignored below WARNING")
        self.assertIs(logging.Logger.handle, original)
        self.assertEqual(log["entries"], [{"logger": "adkl.test.isolated", "level": "ERROR",
                                           "message": "startup_failed lane=%s", "exception": None}])

    def test_withholds_unparameterized_text_and_exception_messages(self):
        logger = logging.getLogger("adkl.test.private")
        with construction_log.capture() as log:
            logger.warning("token=abc123 rejected")  # pre-formatted: may be data
            logger.warning("catalog_reload_skipped token=abc123")
            try:
                raise RuntimeError("password=hunter2")
            except RuntimeError:
                logger.exception("startup failed for %s", "secret-account")
        text = str(log)
        for secret in ("abc123", "hunter2", "secret-account"):
            self.assertNotIn(secret, text)
        messages = [e["message"] for e in log["entries"]]
        self.assertEqual(messages, [None, "catalog_reload_skipped", "startup failed for %s"])
        self.assertEqual(log["entries"][2]["exception"], "RuntimeError")

    def test_deduplicates_and_bounds_entries(self):
        logger = logging.getLogger("adkl.test.bounded")
        with construction_log.capture() as log:
            for index in range(construction_log.LIMIT + 5):
                logger.warning("repeat %s", index)
                logger.warning("event_%s_seen key=%%s" % index, index)
        self.assertEqual(len(log["entries"]), construction_log.LIMIT)
        self.assertEqual(log["entries"][0]["message"], "repeat %s")
        self.assertGreater(log["dropped"], 0)

    def test_findings_severity_is_advisory(self):
        raw = construction_log.findings({"constructionLog": {"dropped": 2, "entries": [
            {"logger": "a", "level": "CRITICAL", "message": None, "exception": "ValueError"},
            {"logger": "b", "level": "WARNING", "message": "x_y", "exception": None}]}})
        self.assertEqual([(f["rule"], f["severity"]) for f in raw], [
            ("FACTORY_CONSTRUCTION_ERROR", "WARN"), ("FACTORY_CONSTRUCTION_WARNING", "INFO"),
            ("FACTORY_CONSTRUCTION_WARNING", "INFO")])
        self.assertIn("message withheld", raw[0]["message"])
        self.assertEqual(construction_log.findings({}), [])
        self.assertEqual(construction_log.findings({"constructionLog": None}), [])


if __name__ == "__main__":
    unittest.main()
