import system_verification_suite
import unittest
import io
import sys

def run():
    suite = unittest.TestLoader().loadTestsFromModule(system_verification_suite)
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)
    
    print("Run finished.")
    print(f"Errors: {len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    
    for fail in result.failures:
        print("\nFAILURE IN:", fail[0])
        print(fail[1])
        
    for err in result.errors:
        print("\nERROR IN:", err[0])
        print(err[1])
        
if __name__ == "__main__":
    run()
