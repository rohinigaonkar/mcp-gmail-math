import os
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
import asyncio
from google import genai
#from google.genai import types
from concurrent.futures import TimeoutError
from functools import partial
import sys

# Load environment variables from .env file
load_dotenv()



# Access your API key and initialize Gemini client correctly
api_key = os.getenv("GEMINI_API_KEY")
email_id = os.getenv("EMAIL_ID")
client = genai.Client(api_key=api_key)


max_iterations = 6
last_response = None
iteration = 0
iteration_response = []

async def generate_with_timeout(client, prompt, timeout=10):
    """Generate content with a timeout"""
    print("Starting LLM generation...")
    try:
        # Convert the synchronous generate_content call to run in a thread
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None, 
                lambda: client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt
                )
            ),
            timeout=timeout
        )
        print("LLM generation completed")
        return response
    except TimeoutError:
        print("LLM generation timed out!")
        raise
    except Exception as e:
        print(f"Error in LLM generation: {e}")
        raise

def reset_state():
    """Reset all global variables to their initial state"""
    global last_response, iteration, iteration_response
    last_response = None
    iteration = 0
    iteration_response = []

async def main():
    reset_state()  # Reset at the start of main
    print("Starting main execution...")
    try:
        # Create a Math MCP server connection
        print("Establishing connection to FirstMCP server for Math...")
        server_params = StdioServerParameters(
            command="python",
            args=["math_mcp_server.py"]
        )

        # Create a Email MCP server connection

        print("Establishing connection to Second MCP server for Email...")
        server_params2 = StdioServerParameters(
            command="python",
            args=["gmail_mcp_server.py", "--creds-file-path", "./credentials.json", "--token-path", "./token.json"]
        )

        tools=[]
        calculator_tools=[]
        gmail_tools=[]

        # Open both sessions and keep them open
        async with stdio_client(server_params2) as (read2, write2), \
                 stdio_client(server_params) as (read, write):
            print("Connections established, creating sessions...")
            async with ClientSession(read2, write2) as session2, \
                     ClientSession(read, write) as session:
                print("Sessions created, initializing...")
                await session2.initialize()
                await session.initialize()
                
                # Get available tools from both sessions
                print("Requesting tool list...")
                tools_result2 = await session2.list_tools()
                gmail_tools = [tool for tool in tools_result2.tools]
                tools = gmail_tools.copy()

                tools_result = await session.list_tools()
                calculator_tools = [tool for tool in tools_result.tools]
                tools.extend(calculator_tools)

                print(f"Successfully retrieved {len(tools)} tools")

                # Create system prompt with available tools
                print("Creating system prompt...")
                print(f"Number of tools: {len(tools)}")
                
                tools_description = []
                
                try:
                    for i, tool in enumerate(calculator_tools):
                        try:
                            # Get tool properties
                            params = tool.inputSchema
                            desc = getattr(tool, 'description', 'No description available')
                            name = getattr(tool, 'name', f'calculator_tool_{i}')
                            
                            # Format the input schema in a more readable way
                            if 'properties' in params:
                                param_details = []
                                for param_name, param_info in params['properties'].items():
                                    param_type = param_info.get('type', 'unknown')
                                    param_details.append(f"{param_name}: {param_type}")
                                params_str = ', '.join(param_details)
                            else:
                                params_str = 'no parameters'

                            tool_desc = f"{i+1}. Calculator - {name}({params_str}) - {desc}"
                            tools_description.append(tool_desc)
                            print(f"{tool_desc}")
                        except Exception as e:
                            print(f"Error processing calculator tool {i}: {e}")
                            tools_description.append(f"{i+1}. Error processing calculator tool")
                    
                    for i, tool in enumerate(gmail_tools):
                        try:
                            # Get tool properties
                            params = tool.inputSchema
                            desc = getattr(tool, 'description', 'No description available')
                            name = getattr(tool, 'name', f'gmail_tool_{i}')
                            
                            # Format the input schema in a more readable way
                            if 'properties' in params:
                                param_details = []
                                for param_name, param_info in params['properties'].items():
                                    param_type = param_info.get('type', 'unknown')
                                    param_details.append(f"{param_name}: {param_type}")
                                params_str = ', '.join(param_details)
                            else:
                                params_str = 'no parameters'

                            tool_desc = f"{i+1}. Gmail - {name}({params_str}) - {desc}"
                            tools_description.append(tool_desc)
                            print(f"{tool_desc}")
                        except Exception as e:
                            print(f"Error processing Gmail tool {i}: {e}")
                            tools_description.append(f"{i+1}. Error processing Gmail tool")
                    
                    tools_description = "\n".join(tools_description)
                    print("Successfully created tools description")
                except Exception as e:
                    print(f"Error creating tools description: {e}")
                    tools_description = "Error loading tools"
                
                print("Created system prompt...")
                
                system_prompt = f"""You are a math agent solving problems in iterations and send email. You have access to various mathematical tools.

                Available tools:
                {tools_description}

                You must respond with EXACTLY ONE line in one of these formats (no additional text):
                1. For function calls:
                FUNCTION_CALL: mcp_server|function_name|param1|param2|...
                
                2. For final answers:
                FINAL_ANSWER: [number]

                Examples:
                - FUNCTION_CALL: Calculator|add|5|3
                - FUNCTION_CALL: Calculator|strings_to_chars_to_int|INDIA
                - FUNCTION_CALL: Calculator|mac_add_text_in_keynote|42
                - FUNCTION_CALL: Gmail|send_email|x.y@gmail.com|Test Email|test message
                - FINAL_ANSWER: [42]

                Important:
                - When a function returns multiple values, you need to process all of them.
                - Only give FINAL_ANSWER when you have completed all necessary calculations AND send email to the recipient {email_id}, with appropriate subject based on the query and body is the calculatedfinal answer text.
                - Do not repeat function calls with the same parameters.
                - Do not add parentheses to the function name.
                - DO NOT include any explanations or additional text.
                - Your entire response should be a single line starting with either FUNCTION_CALL: or FINAL_ANSWER:
                - If user asks non-mathematical queries, you must respond with "I'm sorry, I can only help with mathematical queries."

                """

                # Get query from command line arguments or use default
                default_query = """Find the ASCII values of characters in INDIA and then return sum of exponentials of those values. """
                query = """ """
                query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else default_query
                
                print("Starting iteration loop...")
                
                # Use global iteration variables
                global iteration, last_response
                
                while iteration < max_iterations:
                    print(f"\n--- Iteration {iteration + 1} ---")
                    if last_response is None:
                        current_query = query
                    else:
                        current_query = current_query + "\n\n" + " ".join(iteration_response)
                        current_query = current_query + "  What should I do next?"

                    # Get model's response with timeout
                    print("Preparing to generate LLM response...")
                    prompt = f"{system_prompt}\n\nQuery: {current_query}"
                    try:
                        response = await generate_with_timeout(client, prompt)
                        response_text = response.text.strip()
                        print(f"LLM Response: {response_text}")
                        
                        # Find the FUNCTION_CALL line in the response
                        for line in response_text.split('\n'):
                            line = line.strip()
                            if line.startswith("FUNCTION_CALL:"):
                                response_text = line
                                break
                        
                    except Exception as e:
                        print(f"Failed to get LLM response: {e}")
                        break

                    if response_text.startswith("FUNCTION_CALL:"):
                        _, function_info = response_text.split(":", 1)
                        parts = [p.strip() for p in function_info.split("|")]
                        server_type, func_name, *params = parts
                        
                        print(f"\nDEBUG: Raw function info: {function_info}")
                        print(f"DEBUG: Split parts: {parts}")
                        print(f"DEBUG: Server type: {server_type}")
                        print(f"DEBUG: Function name: {func_name}")
                        print(f"DEBUG: Raw parameters: {params}")
                        
                        try:
                            # Find the matching tool to get its input schema
                            if server_type == "Calculator":
                                tool = next((t for t in calculator_tools if t.name == func_name), None)
                            elif server_type == "Gmail":
                                tool = next((t for t in gmail_tools if t.name == func_name), None)
                            else:
                                raise ValueError(f"Unknown server type: {server_type}")

                            if not tool:
                                print(f"DEBUG: Available tools: {[t.name for t in tools]}")
                                raise ValueError(f"Unknown tool: {func_name}")

                            print(f"DEBUG: Found tool: {tool.name}")
                            print(f"DEBUG: Tool schema: {tool.inputSchema}")

                            # Prepare arguments according to the tool's input schema
                            arguments = {}
                            schema_properties = tool.inputSchema.get('properties', {})
                            print(f"DEBUG: Schema properties: {schema_properties}")

                            for param_name, param_info in schema_properties.items():
                                if not params:  # Check if we have enough parameters
                                    raise ValueError(f"Not enough parameters provided for {func_name}")
                                    
                                value = params.pop(0)  # Get and remove the first parameter
                                param_type = param_info.get('type', 'string')
                                
                                print(f"DEBUG: Converting parameter {param_name} with value {value} to type {param_type}")
                                
                                # Convert the value to the correct type based on the schema
                                if param_type == 'integer':
                                    arguments[param_name] = int(value)
                                elif param_type == 'number':
                                    arguments[param_name] = float(value)
                                elif param_type == 'array':
                                    # Handle array input
                                    if isinstance(value, str):
                                        value = value.strip('[]').split(',')
                                    arguments[param_name] = [int(x.strip()) for x in value]
                                else:
                                    arguments[param_name] = str(value)

                            print(f"DEBUG: Final arguments: {arguments}")
                            print(f"DEBUG: Calling tool {func_name}")
                            
                            # Route the call to the appropriate session based on server type
                            if server_type == "Calculator":
                                result = await session.call_tool(func_name, arguments=arguments)
                            elif server_type == "Gmail":
                                result = await session2.call_tool(func_name, arguments=arguments)
                            else:
                                raise ValueError(f"Unknown server type: {server_type}")
                            
                            print(f"DEBUG: Raw result: {result}")
                            
                            # Get the full result content
                            if hasattr(result, 'content'):
                                print(f"DEBUG: Result has content attribute")
                                # Handle multiple content items
                                if isinstance(result.content, list):
                                    iteration_result = [
                                        item.text if hasattr(item, 'text') else str(item)
                                        for item in result.content
                                    ]
                                else:
                                    iteration_result = str(result.content)
                            else:
                                print(f"DEBUG: Result has no content attribute")
                                iteration_result = str(result)
                                
                            print(f"DEBUG: Final iteration result: {iteration_result}")
                            
                            # Format the response based on result type
                            if isinstance(iteration_result, list):
                                result_str = f"[{', '.join(iteration_result)}]"
                            else:
                                result_str = str(iteration_result)
                            
                            iteration_response.append(
                                f"In the {iteration + 1} iteration you called {server_type}.{func_name} with {arguments} parameters, "
                                f"and the function returned {result_str}."
                            )
                            last_response = iteration_result

                            print(f"Iteration_result: {iteration_result}")

                        except Exception as e:
                            print(f"DEBUG: Error details: {str(e)}")
                            print(f"DEBUG: Error type: {type(e)}")
                            import traceback
                            traceback.print_exc()
                            iteration_response.append(f"Error in iteration {iteration + 1}: {str(e)}")
                            break

                    elif response_text.startswith("FINAL_ANSWER:"):
                        print("\n=== Agent Execution Complete ===")
                        break

                    iteration += 1

    except Exception as e:
        print(f"Error in main execution: {e}")
        import traceback
        traceback.print_exc()
    finally:
        reset_state()  # Reset at the end of main

if __name__ == "__main__":
    asyncio.run(main())
    
    
