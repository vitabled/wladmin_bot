import random
import secrets
import string
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CaptchaType(str, Enum):
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
        """Get answer options (correct + 3 wrong)."""
        wrong_answers = set()
        while len(wrong_answers) < 3:
            wrong = random.randint(
                max(0, self.answer - 5), self.answer + 5
            )
            if wrong != self.answer:
                wrong_answers.add(wrong)

        options = [self.answer] + list(wrong_answers)
        random.shuffle(options)
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
        """Generate a simple math captcha."""
        num1 = secrets.randbelow(20) + 1
        num2 = secrets.randbelow(20) + 1
        operation = secrets.choice(["+", "-", "*"])

        if operation == "+":
            answer = num1 + num2
        elif operation == "-":
            answer = num1 - num2
        else:
            answer = num1 * num2

        return MathCaptcha(num1, num2, operation, answer)

    @staticmethod
    def generate_emoji_captcha() -> tuple[str, list[str]]:
        """Generate emoji captcha: correct emoji + 3 wrong options."""
        correct = secrets.choice(CaptchaService.EMOJI_SET)
        wrong = secrets.SystemRandom().sample(
            [e for e in CaptchaService.EMOJI_SET if e != correct], 3
        )

        options = [correct] + wrong
        secrets.SystemRandom().shuffle(options)
        return correct, options

    @staticmethod
    def verify_button_captcha(user_id: int, target_user_id: int) -> bool:
        """Verify button captcha (simple check that correct user pressed)."""
        return user_id == target_user_id

    @staticmethod
    def verify_math_captcha(
        user_answer: str, correct_answer: int
    ) -> bool:
        """Verify math captcha answer."""
        try:
            return int(user_answer) == correct_answer
        except (ValueError, TypeError):
            return False

    @staticmethod
    def verify_emoji_captcha(user_choice: str, correct_emoji: str) -> bool:
        """Verify emoji captcha choice."""
        return user_choice == correct_emoji
