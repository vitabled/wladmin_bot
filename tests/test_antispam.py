from bot.services.antispam import AntispamService


class TestAntispamService:
    """Tests for antispam detection logic."""

    def test_has_link_http(self):
        """Test detection of HTTP/HTTPS links."""
        assert AntispamService.has_link("Check this http://example.com")
        assert AntispamService.has_link("Visit https://google.com now")

    def test_has_link_telegram(self):
        """Test detection of t.me links."""
        assert AntispamService.has_link("Join us at t.me/mychannel")

    def test_has_link_mention(self):
        """Test detection of channel mentions."""
        assert AntispamService.has_link("Hey @MyChannel please")

    def test_has_link_false(self):
        """Test non-link messages."""
        assert not AntispamService.has_link("Just a regular message")
        assert not AntispamService.has_link("")

    def test_has_forward_true(self):
        """Test forward detection."""
        forward_origin = object()
        assert AntispamService.has_forward(forward_origin)

    def test_has_forward_none(self):
        """Test non-forwarded message."""
        assert not AntispamService.has_forward(None)

    def test_has_stopword_match(self):
        """Test stopword matching."""
        stopwords = ["spam", "bad", "evil"]
        assert AntispamService.has_stopword("this is spam message", stopwords) == "spam"

    def test_has_stopword_case_insensitive(self):
        """Test case-insensitive stopword matching."""
        stopwords = ["spam"]
        assert AntispamService.has_stopword("This is SPAM message", stopwords)

    def test_has_stopword_with_punctuation(self):
        """Test stopword matching with punctuation."""
        stopwords = ["spam"]
        assert AntispamService.has_stopword("This is spam!!! message", stopwords)

    def test_has_stopword_no_match(self):
        """Test no stopword match."""
        stopwords = ["spam"]
        assert not AntispamService.has_stopword("this is clean message", stopwords)

    def test_check_message_all_clean(self):
        """Test clean message."""
        is_spam, reason = AntispamService.check_message(
            "Hello world",
            filter_links=True,
            filter_forwards=True,
            filter_stopwords=True,
            stopwords=["spam"],
        )
        assert not is_spam
        assert reason is None

    def test_check_message_link_detected(self):
        """Test spam detection via link."""
        is_spam, reason = AntispamService.check_message(
            "Check http://example.com", filter_links=True
        )
        assert is_spam
        assert reason == "link"

    def test_check_message_forward_detected(self):
        """Test spam detection via forward."""
        is_spam, reason = AntispamService.check_message(
            "Message", forward_origin=object(), filter_forwards=True
        )
        assert is_spam
        assert reason == "forward"

    def test_check_message_stopword_detected(self):
        """Test spam detection via stopword."""
        is_spam, reason = AntispamService.check_message(
            "This is spam",
            filter_stopwords=True,
            stopwords=["spam"],
        )
        assert is_spam
        assert "stopword" in reason

    def test_check_message_filter_disabled(self):
        """Test that disabled filters don't trigger."""
        is_spam, reason = AntispamService.check_message(
            "Check http://example.com",
            filter_links=False,
        )
        assert not is_spam
