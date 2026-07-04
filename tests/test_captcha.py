import pytest

from bot.services.captcha import CaptchaService, CaptchaType, MathCaptcha


class TestMathCaptcha:
    """Tests for MathCaptcha data class."""

    def test_math_captcha_addition(self):
        """Test math captcha with addition."""
        captcha = MathCaptcha(5, 3, "+", 8)
        assert captcha.question() == "5 + 3 = ?"
        assert captcha.answer == 8

    def test_math_captcha_subtraction(self):
        """Test math captcha with subtraction."""
        captcha = MathCaptcha(10, 4, "-", 6)
        assert captcha.question() == "10 - 4 = ?"
        assert captcha.answer == 6

    def test_math_captcha_multiplication(self):
        """Test math captcha with multiplication."""
        captcha = MathCaptcha(3, 4, "*", 12)
        assert captcha.question() == "3 * 4 = ?"
        assert captcha.answer == 12

    def test_math_captcha_options_count(self):
        """Test that math captcha has 4 options."""
        captcha = MathCaptcha(5, 3, "+", 8)
        options = captcha.options()
        assert len(options) == 4
        assert 8 in options

    def test_math_captcha_options_unique(self):
        """Test that math captcha options are unique."""
        captcha = MathCaptcha(5, 3, "+", 8)
        options = captcha.options()
        assert len(set(options)) == 4


class TestCaptchaService:
    """Tests for captcha service logic."""

    def test_generate_math_captcha(self):
        """Test math captcha generation."""
        captcha = CaptchaService.generate_math_captcha()
        assert captcha.num1 >= 1 and captcha.num1 <= 20
        assert captcha.num2 >= 1 and captcha.num2 <= 20
        assert captcha.operation in ["+", "-", "*"]

    def test_generate_emoji_captcha(self):
        """Test emoji captcha generation."""
        correct, options = CaptchaService.generate_emoji_captcha()
        assert correct in CaptchaService.EMOJI_SET
        assert len(options) == 4
        assert correct in options

    def test_verify_button_captcha_match(self):
        """Test button captcha verification - correct user."""
        assert CaptchaService.verify_button_captcha(user_id=123, target_user_id=123)

    def test_verify_button_captcha_mismatch(self):
        """Test button captcha verification - wrong user."""
        assert not CaptchaService.verify_button_captcha(
            user_id=123, target_user_id=456
        )

    def test_verify_math_captcha_correct(self):
        """Test math captcha verification - correct answer."""
        assert CaptchaService.verify_math_captcha("8", 8)

    def test_verify_math_captcha_incorrect(self):
        """Test math captcha verification - incorrect answer."""
        assert not CaptchaService.verify_math_captcha("9", 8)

    def test_verify_math_captcha_invalid_input(self):
        """Test math captcha verification - invalid input."""
        assert not CaptchaService.verify_math_captcha("abc", 8)

    def test_verify_emoji_captcha_correct(self):
        """Test emoji captcha verification - correct choice."""
        emoji = "😀"
        assert CaptchaService.verify_emoji_captcha(emoji, emoji)

    def test_verify_emoji_captcha_incorrect(self):
        """Test emoji captcha verification - incorrect choice."""
        assert not CaptchaService.verify_emoji_captcha("😀", "😂")
