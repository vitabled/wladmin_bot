import secrets
from dataclasses import dataclass
from enum import StrEnum

# Cryptographically-strong RNG (predictable captcha would be trivially bypassed).
_rng = secrets.SystemRandom()


class CaptchaType(StrEnum):
    BUTTON = "button"
    MATH = "math"
    EMOJI = "emoji"


@dataclass
class MathCaptcha:
    """Math captcha data."""

    num1: int
    num2: int
    operation: str
    answer: int

    def question(self) -> str:
        """Get captcha question text."""
        return f"{self.num1} {self.operation} {self.num2} = ?"

    def options(self) -> list[int]:
        """Get 4 shuffled answer options (correct + 3 unique wrong).

        Robust to negative/zero/large answers: distractors are built from
        symmetric offsets around the answer, widening until 3 unique values
        distinct from the answer are found.
        """
        wrong_answers: set[int] = set()
        offset = 1
        while len(wrong_answers) < 3:
            for candidate in (self.answer - offset, self.answer + offset):
                if candidate != self.answer:
                    wrong_answers.add(candidate)
                if len(wrong_answers) >= 3:
                    break
            offset += 1

        options = [self.answer, *list(wrong_answers)[:3]]
        _rng.shuffle(options)
        return options


class CaptchaService:
    """Pure business logic for captcha generation and verification."""

    EMOJI_SET = [
        "😀",
        "😂",
        "😍",
        "🎉",
        "🚀",
        "🎨",
        "🎭",
        "🎪",
    ]

    @staticmethod
    def generate_math_captcha() -> MathCaptcha:
        """Generate a simple math captcha with a non-negative answer."""
        num1 = _rng.randint(1, 20)
        num2 = _rng.randint(1, 20)
        operation = _rng.choice(["+", "-", "*"])

        if operation == "+":
            answer = num1 + num2
        elif operation == "-":
            # Keep the answer non-negative for a friendlier prompt.
            if num2 > num1:
                num1, num2 = num2, num1
            answer = num1 - num2
        else:
            answer = num1 * num2

        return MathCaptcha(num1, num2, operation, answer)

    @staticmethod
    def generate_emoji_captcha() -> tuple[str, list[str]]:
        """Generate emoji captcha: correct emoji + 3 wrong options."""
        correct = _rng.choice(CaptchaService.EMOJI_SET)
        wrong = _rng.sample([e for e in CaptchaService.EMOJI_SET if e != correct], 3)

        options = [correct, *wrong]
        _rng.shuffle(options)
        return correct, options

    @staticmethod
    def verify_button_captcha(user_id: int, target_user_id: int) -> bool:
        """Verify button captcha (simple check that correct user pressed)."""
        return user_id == target_user_id

    @staticmethod
    def verify_math_captcha(user_answer: str, correct_answer: int) -> bool:
        """Verify math captcha answer."""
        try:
            return int(user_answer) == correct_answer
        except (ValueError, TypeError):
            return False

    @staticmethod
    def verify_emoji_captcha(user_choice: str, correct_emoji: str) -> bool:
        """Verify emoji captcha choice."""
        return user_choice == correct_emoji
